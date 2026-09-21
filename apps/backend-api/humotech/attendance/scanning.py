"""Скан QR сотрудником: от строки с экрана до записи в журнале.

Здесь собирается всё, что должно случиться между «человек навёл камеру»
и «отметка засчитана». Само правило входа-выхода живёт в `services.py`;
этот модуль отвечает за то, что до него доходит.

Что решает сервер и никогда не берёт у клиента:

  * **кто** — из проверенной привязки Telegram, а не из тела запроса;
  * **где** — из точки, зашитой в подписанный код;
  * **вход или выход** — из режима точки и наличия открытой сессии;
  * **когда** — время сервера. Часы телефона сюда не попадают вовсе:
    отметку временем клиента подделывал бы любой, кто переведёт часы.

Чего в журнал не попадает: сама строка кода и её подпись. Отклонённая
попытка записывается по существу — точка, причина, время, — но не так,
чтобы из логов можно было собрать рабочий код.

Про честную границу возможного. Ни один из этих механизмов не мешает
человеку сфотографировать действующий код и отправить его коллеге. Без
Face ID, геолокации или турникета это в принципе не решается на уровне
приложения. Что здесь сделано вместо этого: срок кода в полминуты,
обязательная авторизация сотрудника, необязательная проверка офисной сети
и полный журнал попыток. Риск уменьшен, но не снят, и делать вид,
что снят, — хуже, чем сказать прямо.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError
from django.utils import timezone

from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.attendance.services import RejectionReason, register_scan
from humotech.core.clientip import address_in_networks
from humotech.core.errors import constraint_name_of
from humotech.offices.models import OfficeNetwork
from humotech.qr_codes import stickers
from humotech.qr_codes.models import OfficeQrPoint
from humotech.qr_codes.tokens import QrTokenError, read

logger = logging.getLogger("humotech.attendance")


class ScanStatus:
    """Чем закончилась попытка. Один код — один экран в приложении."""

    ENTERED = "ENTERED"
    EXITED = "EXITED"
    ALREADY_INSIDE = "ALREADY_INSIDE"
    NOT_INSIDE = "NOT_INSIDE"
    QR_EXPIRED = "QR_EXPIRED"
    QR_INVALID = "QR_INVALID"
    QR_ALREADY_USED = "QR_ALREADY_USED"
    QR_POINT_INACTIVE = "QR_POINT_INACTIVE"
    OFFICE_NOT_ALLOWED = "OFFICE_NOT_ALLOWED"
    OUTSIDE_GEOFENCE = "OUTSIDE_GEOFENCE"
    LOCATION_TOO_VAGUE = "LOCATION_TOO_VAGUE"
    NETWORK_REQUIRED = "NETWORK_REQUIRED"
    GEOLOCATION_REQUIRED = "GEOLOCATION_REQUIRED"
    # Печатный код, которого больше нет: наклейку перевыпустили или
    # это вовсе не наш секрет. Отличить одно от другого нельзя — старые
    # хеши не хранятся, — и человеку в обоих случаях нужно одно и то же.
    QR_REVOKED = "QR_REVOKED"
    GEOFENCE_NOT_CONFIGURED = "GEOFENCE_NOT_CONFIGURED"
    TOO_SOON = "TOO_SOON"


# Ограничение, по которому узнаётся повторно присланная попытка.
CLIENT_EVENT_CONSTRAINT = "uq_attendance_events_client_event"

# Причина отказа из правила -> состояние экрана. Отдельная таблица, а не
# совпадение имён: у отказов свой словарь, у экранов свой, и связывать их
# написанием одинаковых строк в двух местах — способ однажды разойтись.
_REJECTION_TO_STATUS = {
    RejectionReason.ALREADY_INSIDE: ScanStatus.ALREADY_INSIDE,
    RejectionReason.NOT_INSIDE: ScanStatus.NOT_INSIDE,
    RejectionReason.QR_EXPIRED: ScanStatus.QR_EXPIRED,
    RejectionReason.QR_POINT_INACTIVE: ScanStatus.QR_POINT_INACTIVE,
    RejectionReason.NONCE_REUSED: ScanStatus.QR_ALREADY_USED,
    RejectionReason.OFFICE_NOT_ALLOWED: ScanStatus.OFFICE_NOT_ALLOWED,
    RejectionReason.NETWORK_REQUIRED: ScanStatus.NETWORK_REQUIRED,
    RejectionReason.GEOLOCATION_REQUIRED: ScanStatus.GEOLOCATION_REQUIRED,
    RejectionReason.OUTSIDE_GEOFENCE: ScanStatus.OUTSIDE_GEOFENCE,
    RejectionReason.LOCATION_TOO_VAGUE: ScanStatus.LOCATION_TOO_VAGUE,
    RejectionReason.CLOCK_DRIFT: ScanStatus.QR_EXPIRED,
    RejectionReason.GEOFENCE_NOT_CONFIGURED: ScanStatus.GEOFENCE_NOT_CONFIGURED,
    RejectionReason.TOO_SOON: ScanStatus.TOO_SOON,
}

# Почему код не разобрался -> что показать. Истёкший срок отделён от всего
# остального: человеку надо просто отсканировать ещё раз, и говорить ему
# «код недействителен» в этом случае — врать.
_TOKEN_ERROR_TO_STATUS = {
    "expired": ScanStatus.QR_EXPIRED,
    "issued_in_future": ScanStatus.QR_EXPIRED,
}


@dataclass(frozen=True)
class ScanOutcome:
    status: str
    session: AttendanceSession | None = None
    office_name: str | None = None
    point_name: str | None = None
    occurred_at: datetime | None = None
    #: Направление точки: ENTRY, EXIT или BOTH. По нему бот объясняет
    #: отказ «эта точка только для входа», не угадывая.
    point_mode: str | None = None
    #: Расстояние до офиса в метрах и допустимый радиус — если сравнивали.
    distance_m: float | None = None
    radius_m: int | None = None
    #: Часовой пояс офиса: время отметки человеку показывается в нём.
    office_timezone: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status in (ScanStatus.ENTERED, ScanStatus.EXITED)


def scan(
    context,
    *,
    token: str,
    ip_address: str | None = None,
    client_event_id: str | None = None,
    latitude=None,
    longitude=None,
    accuracy_m=None,
    source: str = "QR",
    now: datetime | None = None,
) -> ScanOutcome:
    """Обработать один скан от сотрудника.

    `context` — проверенный `EmployeeContext`. Сотрудник и организация
    берутся только оттуда: параметров с такими именами здесь нет.

    Координаты необязательны и недоверенны: требует ли их точка и
    достаточно ли они близки, решает правило на сервере.
    """
    moment = now or timezone.now()
    employee = context.employee

    # Печатный код узнаётся по виду раньше подписанного: у него нет
    # подписи, и разбирать его как меняющийся значило бы ответить
    # «код не распознан» на рабочую наклейку.
    secret = stickers.secret_of(token)
    if secret is not None:
        return _scan_sticker(
            context, secret=secret, moment=moment, ip_address=ip_address,
            client_event_id=client_event_id, latitude=latitude,
            longitude=longitude, accuracy_m=accuracy_m, source=source,
        )

    try:
        payload = read(token, now=moment)
    except QrTokenError as error:
        # Ни строки кода, ни подписи в журнале: по ним собирали бы рабочий
        # код из логов. Причина — по имени, этого достаточно для разбора.
        logger.info(
            "qr scan rejected: %s",
            error.reason,
            extra={"employee_id": str(employee.id)},
        )
        return ScanOutcome(
            status=_TOKEN_ERROR_TO_STATUS.get(error.reason, ScanStatus.QR_INVALID)
        )

    point = (
        OfficeQrPoint.objects.select_related("office", "office__organization")
        .filter(id=payload.qr_point_id)
        .first()
    )
    if point is None:
        return ScanOutcome(status=ScanStatus.QR_INVALID)

    # Подпись подтверждает, что код выпустили мы, но не что он до сих пор
    # верен: точку могли перенести в другой офис уже после выпуска.
    if (
        point.organization_id != payload.organization_id
        or point.office_id != payload.office_id
        or point.direction_mode != payload.direction_mode
    ):
        logger.warning(
            "qr scan rejected: token no longer matches its point",
            extra={"qr_point_id": str(point.id)},
        )
        return ScanOutcome(status=ScanStatus.QR_INVALID)

    # Межорганизационный скан запрещён всегда — раньше любых других правил
    # и независимо от того, что разрешено сотруднику внутри его организации.
    if point.organization_id != context.organization_id:
        logger.warning(
            "qr scan rejected: cross-organization attempt",
            extra={
                "employee_id": str(employee.id),
                "qr_point_id": str(point.id),
            },
        )
        return ScanOutcome(status=ScanStatus.OFFICE_NOT_ALLOWED)

    try:
        result = register_scan(
            employee_id=employee.id,
            qr_point=point,
            now=moment,
            # occurred_at не передаётся намеренно: время отметки — серверное.
            qr_nonce_hash=payload.nonce_hash,
            qr_issued_at=payload.issued_at,
            qr_expires_at=payload.expires_at,
            ip_address=ip_address,
            inside_office_network=_inside_office_network(point, ip_address),
            latitude=latitude,
            longitude=longitude,
            location_accuracy_m=accuracy_m,
            client_event_id=client_event_id,
            source=source,
        )
    except IntegrityError as error:
        # Ту же самую попытку прислали второй раз. Так делает автоповтор
        # сети: ответ на первый запрос потерялся по дороге, телефон
        # отправил его заново, а сотрудник не нажимал ничего.
        replayed = _replay(error, employee_id=employee.id,
                           client_event_id=client_event_id, point=point)
        if replayed is None:
            raise
        return replayed

    return _outcome(result, point, employee_id=employee.id)


def _scan_sticker(
    context, *, secret, moment, ip_address, client_event_id, latitude,
    longitude, accuracy_m, source,
) -> ScanOutcome:
    """Скан печатного кода: точка — по хешу секрета, строгая геозона.

    Срока и одноразовости у наклейки нет, поэтому вся защита — в месте:
    координаты обязательны, офис обязан быть на карте, а повтор в
    течение минуты не принимается. Проверку выполняет то же правило
    `register_scan`, что и для меняющегося кода, — с флагом строгости.
    """
    employee = context.employee
    point = (
        OfficeQrPoint.objects.select_related("office", "office__organization")
        .filter(qr_mode="STATIC", static_token_hash=stickers.token_hash(secret))
        .first()
    )
    if point is None:
        # Секрета в логе нет: по нему собирается рабочая наклейка.
        logger.info(
            "sticker scan rejected: unknown or reissued secret",
            extra={"employee_id": str(employee.id)},
        )
        return ScanOutcome(status=ScanStatus.QR_REVOKED)

    if point.organization_id != context.organization_id:
        logger.warning(
            "sticker scan rejected: cross-organization attempt",
            extra={"employee_id": str(employee.id), "qr_point_id": str(point.id)},
        )
        return ScanOutcome(status=ScanStatus.OFFICE_NOT_ALLOWED)

    try:
        result = register_scan(
            employee_id=employee.id,
            qr_point=point,
            now=moment,
            ip_address=ip_address,
            inside_office_network=_inside_office_network(point, ip_address),
            latitude=latitude,
            longitude=longitude,
            location_accuracy_m=accuracy_m,
            client_event_id=client_event_id,
            source=source,
            strict_location=True,
        )
    except IntegrityError as error:
        replayed = _replay(error, employee_id=employee.id,
                           client_event_id=client_event_id, point=point)
        if replayed is None:
            raise
        return replayed

    return _outcome(result, point, employee_id=employee.id)


def _outcome(result, point: OfficeQrPoint, *, employee_id) -> ScanOutcome:
    """Результат правила -> ответ. Один на оба вида кода."""
    event = result.event
    distance = float(event.distance_m) if event.distance_m is not None else None
    common = {
        "office_name": point.office.name,
        "point_name": point.name,
        "point_mode": point.direction_mode,
        "distance_m": distance,
        "radius_m": point.office.geofence_radius_m,
        "office_timezone": point.office.timezone,
    }

    if not result.accepted:
        logger.info(
            "qr scan rejected: %s",
            result.rejection_reason,
            extra={
                "employee_id": str(employee_id),
                "qr_point_id": str(point.id),
            },
        )
        return ScanOutcome(
            status=_REJECTION_TO_STATUS.get(
                result.rejection_reason, ScanStatus.QR_INVALID
            ),
            **common,
        )

    return ScanOutcome(
        status=(
            ScanStatus.ENTERED if event.event_type == "ENTRY" else ScanStatus.EXITED
        ),
        session=result.session,
        occurred_at=event.occurred_at,
        **common,
    )


def _replay(
    error: IntegrityError,
    *,
    employee_id,
    client_event_id: str | None,
    point: OfficeQrPoint,
) -> ScanOutcome | None:
    """Ответ на повторно присланную попытку — тот же, что и на первую.

    Вторая запись не появляется: её не пускает частичный уникальный
    индекс по паре «сотрудник + идентификатор попытки». Но отдавать в
    ответ ошибку целостности нельзя. Человек у двери увидел бы отказ на
    отметке, которая уже прошла, и приложил бы пропуск ещё раз — а вот
    там его встретил бы `QR_ALREADY_USED`, потому что код одноразовый.

    Возвращается `None`, если нарушено какое-то другое ограничение: тогда
    исключение должно идти дальше, а не превращаться в чужой ответ.
    """
    if constraint_name_of(error) != CLIENT_EVENT_CONSTRAINT or not client_event_id:
        return None

    # Транзакция `register_scan` откатилась целиком, и читать нужно уже
    # в новой: первая попытка сохранена своей, до этого запроса.
    first = (
        AttendanceEvent.objects.filter(
            employee_id=employee_id, client_event_id=client_event_id
        )
        .order_by("received_at")
        .first()
    )
    if first is None:
        # Строка есть по мнению индекса, но не читается: гоняться за этим
        # состоянием не следует, пусть ошибка идёт дальше как есть.
        return None

    logger.info(
        "qr scan replayed: same attempt sent twice",
        extra={"employee_id": str(employee_id)},
    )

    common = {
        "office_name": point.office.name,
        "point_name": point.name,
        "point_mode": point.direction_mode,
        "distance_m": (
            float(first.distance_m) if first.distance_m is not None else None
        ),
        "radius_m": point.office.geofence_radius_m,
        "office_timezone": point.office.timezone,
    }

    if first.verification_status != "ACCEPTED":
        return ScanOutcome(
            status=_REJECTION_TO_STATUS.get(
                first.rejection_reason, ScanStatus.QR_INVALID
            ),
            **common,
        )

    session = AttendanceSession.objects.filter(
        entry_event_id=first.id
    ).first() or AttendanceSession.objects.filter(exit_event_id=first.id).first()

    return ScanOutcome(
        status=(
            ScanStatus.ENTERED if first.event_type == "ENTRY" else ScanStatus.EXITED
        ),
        session=session,
        occurred_at=first.occurred_at,
        **common,
    )


def _inside_office_network(point: OfficeQrPoint, ip_address: str | None):
    """Внутри ли сотрудник офисной сети — если офис вообще её описал.

    `None` означает «не проверялось», и это не то же самое, что `False`.
    Точка, не требующая офисной сети, не должна помечать все отметки как
    сделанные снаружи: в журнале это выглядело бы как нарушение.
    """
    if not point.require_office_network:
        return None

    networks = OfficeNetwork.objects.filter(
        office_id=point.office_id, is_active=True
    ).values_list("network_cidr", flat=True)
    return address_in_networks(ip_address, networks)


__all__ = ["ScanOutcome", "ScanStatus", "scan"]
