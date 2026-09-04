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

from django.utils import timezone

from humotech.attendance.models import AttendanceSession
from humotech.attendance.services import RejectionReason, register_scan
from humotech.core.clientip import address_in_networks
from humotech.offices.models import OfficeNetwork
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
    NETWORK_REQUIRED = "NETWORK_REQUIRED"
    GEOLOCATION_REQUIRED = "GEOLOCATION_REQUIRED"


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
    RejectionReason.CLOCK_DRIFT: ScanStatus.QR_EXPIRED,
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

    @property
    def accepted(self) -> bool:
        return self.status in (ScanStatus.ENTERED, ScanStatus.EXITED)


def scan(
    context,
    *,
    token: str,
    ip_address: str | None = None,
    client_event_id: str | None = None,
    now: datetime | None = None,
) -> ScanOutcome:
    """Обработать один скан от сотрудника.

    `context` — проверенный `EmployeeContext`. Сотрудник и организация
    берутся только оттуда: параметров с такими именами здесь нет.
    """
    moment = now or timezone.now()
    employee = context.employee

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
        client_event_id=client_event_id,
        source="QR",
    )

    if not result.accepted:
        logger.info(
            "qr scan rejected: %s",
            result.rejection_reason,
            extra={
                "employee_id": str(employee.id),
                "qr_point_id": str(point.id),
            },
        )
        return ScanOutcome(
            status=_REJECTION_TO_STATUS.get(
                result.rejection_reason, ScanStatus.QR_INVALID
            ),
            office_name=point.office.name,
            point_name=point.name,
        )

    return ScanOutcome(
        status=(
            ScanStatus.ENTERED
            if result.event.event_type == "ENTRY"
            else ScanStatus.EXITED
        ),
        session=result.session,
        office_name=point.office.name,
        point_name=point.name,
        occurred_at=result.event.occurred_at,
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
