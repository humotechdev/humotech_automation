"""Серверная обработка QR-скана: решение принимает бэкенд, а не клиент.

Это НЕ сканер и не HTTP-слой — здесь только правило, которое превращает
попытку отметки в запись журнала и, при успехе, в рабочую сессию.

Что бэкенд определяет сам и никогда не берёт у клиента:
  * офис            — из `office_qr_points.office_id`;
  * направление     — из режима точки, а при BOTH из наличия открытой сессии;
  * результат       — `verification_status` ставится здесь и только здесь.

Любая попытка, включая отклонённую, попадает в `attendance_events`:
это основной материал для расследования инцидентов.

Два одновременных скана одного человека выстраиваются в очередь: обработка
идёт в транзакции под блокировкой строки сотрудника. Без неё два запроса,
пришедшие в один момент, оба увидели бы «открытой сессии нет» и создали бы
две. Частичный уникальный ключ `uq_attendance_sessions_one_open` — второй
рубеж на случай, если запись пойдёт мимо этого модуля.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from humotech.employees.models import (
    Employee,
    EmployeeAssignment,
    EmployeeOfficeAccess,
)
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.offices.geo import distance_m, looks_like_coordinates, within_office
from humotech.qr_codes.models import OfficeQrPoint

# насколько время клиента может отличаться от серверного
MAX_CLOCK_DRIFT = timedelta(minutes=5)


class RejectionReason:
    QR_EXPIRED = "QR_EXPIRED"
    QR_POINT_INACTIVE = "QR_POINT_INACTIVE"
    NONCE_REUSED = "NONCE_REUSED"
    OFFICE_NOT_ALLOWED = "OFFICE_NOT_ALLOWED"
    ALREADY_INSIDE = "ALREADY_INSIDE"
    NOT_INSIDE = "NOT_INSIDE"
    GEOLOCATION_REQUIRED = "GEOLOCATION_REQUIRED"
    # Координаты пришли, но человек не там.
    OUTSIDE_GEOFENCE = "OUTSIDE_GEOFENCE"
    # Координаты пришли, но такой погрешности верить нельзя.
    LOCATION_TOO_VAGUE = "LOCATION_TOO_VAGUE"
    NETWORK_REQUIRED = "NETWORK_REQUIRED"
    CLOCK_DRIFT = "CLOCK_DRIFT"
    # Печатный код: у офиса нет точки на карте или радиуса, и проверить,
    # на месте ли человек, не с чем. Такой код не принимается вовсе —
    # иначе наклейка, сфотографированная у двери, работала бы из дома.
    GEOFENCE_NOT_CONFIGURED = "GEOFENCE_NOT_CONFIGURED"
    # Печатный код одноразовости не имеет: второй скан той же наклейки
    # сразу после первого у точки «вход и выход» закрыл бы только что
    # открытую сессию.
    TOO_SOON = "TOO_SOON"


@dataclass(frozen=True)
class ScanResult:
    event: AttendanceEvent
    session: AttendanceSession | None
    accepted: bool
    rejection_reason: str | None


def employee_may_use_office(
    *,
    employee_id: uuid.UUID,
    office_id: uuid.UUID,
    at: datetime,
) -> bool:
    """Право сотрудника отмечаться в этом офисе на момент `at`.

    Основание — либо действующее основное назначение в этот офис,
    либо явно выданный доступ в `employee_office_access`.
    """
    day = at.date()

    assigned = EmployeeAssignment.objects.filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=day),
        employee_id=employee_id,
        office_id=office_id,
        valid_from__lte=day,
    ).exists()
    if assigned:
        return True

    return EmployeeOfficeAccess.objects.filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=at),
        employee_id=employee_id,
        office_id=office_id,
        valid_from__lte=at,
    ).exists()


def open_session_for(*, employee_id: uuid.UUID) -> AttendanceSession | None:
    return AttendanceSession.objects.filter(
        employee_id=employee_id, status="OPEN"
    ).first()


def _nonce_already_used(*, employee_id: uuid.UUID, nonce_hash: str) -> bool:
    """Использовал ли этот сотрудник этот код — в любом направлении.

    Направление здесь намеренно НЕ учитывается, хотя уникальный ключ в базе
    учитывает. У точки в режиме BOTH один и тот же код иначе засчитался бы
    дважды: сначала как вход, потом как выход. При сроке в полминуты это
    рабочий сценарий, а не теоретический — достаточно отсканировать код
    второй раз, выходя из кадра.

    Ключ в базе остаётся более узким: он второй рубеж, а не первый, и
    сужать его до `(сотрудник, код)` значило бы менять схему ради того,
    что дешевле проверить здесь.
    """
    return AttendanceEvent.objects.filter(
        employee_id=employee_id,
        qr_nonce_hash=nonce_hash,
        verification_status="ACCEPTED",
    ).exists()


def register_scan(
    *,
    employee_id: uuid.UUID,
    qr_point: OfficeQrPoint,
    now: datetime,
    occurred_at: datetime | None = None,
    qr_nonce_hash: str | None = None,
    qr_issued_at: datetime | None = None,
    qr_expires_at: datetime | None = None,
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
    location_accuracy_m: Decimal | None = None,
    ip_address: str | None = None,
    inside_office_network: bool | None = None,
    inside_geofence: bool | None = None,
    employee_device_id: uuid.UUID | None = None,
    qr_display_session_id: uuid.UUID | None = None,
    client_event_id: str | None = None,
    source: str = "QR",
    strict_location: bool = False,
) -> ScanResult:
    """Обрабатывает одну попытку отметки и возвращает записанное событие.

    `strict_location` — для печатного кода: без координат, без геозоны у
    офиса и повторно в течение минуты отметка не принимается. У
    меняющегося кода на экране эти проверки мягче: его защищают подпись,
    срок в полминуты и одноразовость.
    """
    with transaction.atomic():
        return _register_locked(
            employee_id=employee_id,
            qr_point=qr_point,
            now=now,
            occurred_at=occurred_at,
            qr_nonce_hash=qr_nonce_hash,
            qr_issued_at=qr_issued_at,
            qr_expires_at=qr_expires_at,
            latitude=latitude,
            longitude=longitude,
            location_accuracy_m=location_accuracy_m,
            ip_address=ip_address,
            inside_office_network=inside_office_network,
            inside_geofence=inside_geofence,
            employee_device_id=employee_device_id,
            qr_display_session_id=qr_display_session_id,
            client_event_id=client_event_id,
            source=source,
            strict_location=strict_location,
        )


def _register_locked(
    *,
    employee_id: uuid.UUID,
    qr_point: OfficeQrPoint,
    now: datetime,
    occurred_at: datetime | None,
    qr_nonce_hash: str | None,
    qr_issued_at: datetime | None,
    qr_expires_at: datetime | None,
    latitude: Decimal | None,
    longitude: Decimal | None,
    location_accuracy_m: Decimal | None,
    ip_address: str | None,
    inside_office_network: bool | None,
    inside_geofence: bool | None,
    employee_device_id: uuid.UUID | None,
    qr_display_session_id: uuid.UUID | None,
    client_event_id: str | None,
    source: str,
    strict_location: bool = False,
) -> ScanResult:
    """Тело обработки. Вызывается только внутри транзакции."""
    # Блокировка строки сотрудника выстраивает его сканы в очередь. Без неё
    # два одновременных запроса оба увидели бы «открытой сессии нет».
    # Блокируется именно сотрудник, а не сессия: сессии может ещё не быть,
    # и блокировать было бы нечего.
    Employee.objects.select_for_update().filter(id=employee_id).first()

    occurred_at = occurred_at or now
    office_id = qr_point.office_id  # офис берём ТОЛЬКО отсюда

    current_session = open_session_for(employee_id=employee_id)

    # направление: у точки ENTRY/EXIT оно фиксировано, у BOTH — по факту
    if qr_point.direction_mode in ("ENTRY", "EXIT"):
        event_type = qr_point.direction_mode
    else:
        event_type = "EXIT" if current_session is not None else "ENTRY"

    # Считается здесь, а не берётся у вызывающего: «внутри ли» — вывод из
    # координат и радиуса офиса, и делать его должно одно место.
    if inside_geofence is None:
        inside_geofence = location_check(
            qr_point, latitude, longitude, location_accuracy_m
        )
    distance = location_distance(qr_point, latitude, longitude)

    reason = _reject_reason(
        employee_id=employee_id,
        qr_point=qr_point,
        event_type=event_type,
        now=now,
        occurred_at=occurred_at,
        qr_nonce_hash=qr_nonce_hash,
        qr_expires_at=qr_expires_at,
        latitude=latitude,
        longitude=longitude,
        location_accuracy_m=location_accuracy_m,
        inside_office_network=inside_office_network,
        current_session=current_session,
        strict_location=strict_location,
    )
    accepted = reason is None

    event = AttendanceEvent.objects.create(
        organization_id=qr_point.organization_id,
        employee_id=employee_id,
        office_id=office_id,
        qr_point_id=qr_point.id,
        qr_display_session_id=qr_display_session_id,
        employee_device_id=employee_device_id,
        event_type=event_type,
        source=source,
        verification_status="ACCEPTED" if accepted else "REJECTED",
        occurred_at=occurred_at,
        received_at=now,
        qr_nonce_hash=qr_nonce_hash,
        qr_issued_at=qr_issued_at,
        qr_expires_at=qr_expires_at,
        latitude=latitude,
        longitude=longitude,
        location_accuracy_m=location_accuracy_m,
        ip_address=ip_address,
        inside_geofence=inside_geofence,
        distance_m=(
            Decimal(str(round(distance, 2))) if distance is not None else None
        ),
        inside_office_network=inside_office_network,
        client_event_id=client_event_id,
        rejection_reason=reason,
    )

    # Отклонённая попытка НИКОГДА не трогает рабочие сессии.
    if not accepted:
        return ScanResult(event=event, session=None, accepted=False,
                          rejection_reason=reason)

    if event_type == "ENTRY":
        work_session = AttendanceSession.objects.create(
            organization_id=qr_point.organization_id,
            employee_id=employee_id,
            office_id=office_id,
            entry_event_id=event.id,
            started_at=occurred_at,
            status="OPEN",
        )
    else:
        work_session = current_session
        work_session.exit_event_id = event.id
        work_session.ended_at = occurred_at
        work_session.duration_seconds = int(
            (occurred_at - work_session.started_at).total_seconds()
        )
        work_session.status = "CLOSED"
        work_session.calculated_at = now
        work_session.save()

    return ScanResult(event=event, session=work_session, accepted=True,
                      rejection_reason=None)


def _reject_reason(
    *,
    employee_id: uuid.UUID,
    qr_point: OfficeQrPoint,
    event_type: str,
    now: datetime,
    occurred_at: datetime,
    qr_nonce_hash: str | None,
    qr_expires_at: datetime | None,
    latitude: Decimal | None,
    longitude: Decimal | None,
    location_accuracy_m: Decimal | None,
    inside_office_network: bool | None,
    current_session: AttendanceSession | None,
    strict_location: bool = False,
) -> str | None:
    """Первая сработавшая причина отказа, либо None если всё в порядке."""
    if not qr_point.is_active or qr_point.archived_at is not None:
        return RejectionReason.QR_POINT_INACTIVE

    if qr_expires_at is not None and now > qr_expires_at:
        return RejectionReason.QR_EXPIRED

    # время клиента проверяем относительно серверного
    if abs(occurred_at - now) > MAX_CLOCK_DRIFT:
        return RejectionReason.CLOCK_DRIFT

    if qr_nonce_hash is not None and _nonce_already_used(
        employee_id=employee_id, nonce_hash=qr_nonce_hash
    ):
        return RejectionReason.NONCE_REUSED

    if not employee_may_use_office(
        employee_id=employee_id, office_id=qr_point.office_id, at=now
    ):
        return RejectionReason.OFFICE_NOT_ALLOWED

    if strict_location:
        # Печатный код без координат не принимается: иначе фотография
        # наклейки работала бы откуда угодно.
        if latitude is None or longitude is None:
            return RejectionReason.GEOLOCATION_REQUIRED
        if not office_is_located(qr_point.office):
            return RejectionReason.GEOFENCE_NOT_CONFIGURED

    if qr_point.require_geolocation and (latitude is None or longitude is None):
        return RejectionReason.GEOLOCATION_REQUIRED

    # Координаты прислали — проверяем их независимо от того, требует их
    # точка или нет. Присланное и негодное хуже неприсланного: молча
    # принять «я в трёх километрах» значит записать это в журнал как
    # обычную отметку.
    if latitude is not None and longitude is not None:
        geo = _location_verdict(qr_point, latitude, longitude, location_accuracy_m)
        if geo is not None:
            return geo

    if qr_point.require_office_network and not inside_office_network:
        return RejectionReason.NETWORK_REQUIRED

    # Повтор проверяется ПОСЛЕ места: тому, кто отошёл от офиса, важнее
    # услышать «слишком далеко», чем «вы только что отметились».
    if strict_location and _repeated_too_soon(
        employee_id=employee_id, qr_point=qr_point, now=now
    ):
        return RejectionReason.TOO_SOON

    if event_type == "ENTRY" and current_session is not None:
        return RejectionReason.ALREADY_INSIDE

    if event_type == "EXIT" and current_session is None:
        return RejectionReason.NOT_INSIDE

    return None


def _location_verdict(qr_point, latitude, longitude, accuracy_m) -> str | None:
    """Причина отказа по местоположению, либо None.

    Порядок проверок: сперва вообще похоже ли это на точку на Земле,
    потом — можно ли верить погрешности, и только потом расстояние.
    Считать расстояние от мусора бессмысленно, а отвечать «слишком
    далеко» на широту 900 — врать про причину.

    Офис без координат или без радиуса проверку не проходит и не
    заваливает: сравнивать не с чем, и записывать это человеку в вину
    нельзя. `inside_geofence` в таком случае остаётся `None` —
    «не проверялось», а не «нарушение».
    """
    if not looks_like_coordinates(latitude, longitude):
        return RejectionReason.LOCATION_TOO_VAGUE

    if accuracy_m is not None:
        try:
            accuracy = Decimal(str(accuracy_m))
        except (ArithmeticError, TypeError, ValueError):
            return RejectionReason.LOCATION_TOO_VAGUE
        ceiling = settings.QR["MAX_LOCATION_ACCURACY_M"]
        if not accuracy.is_finite() or accuracy <= 0 or accuracy > ceiling:
            return RejectionReason.LOCATION_TOO_VAGUE

    verdict = within_office(
        qr_point.office, latitude, longitude, accuracy_m=accuracy_m
    )
    if verdict.checked and not verdict.inside:
        return RejectionReason.OUTSIDE_GEOFENCE
    return None


def office_is_located(office) -> bool:
    """Есть ли у офиса точка на карте и радиус — то, с чем сравнивать."""
    return (
        office is not None
        and office.latitude is not None
        and office.longitude is not None
        and bool(office.geofence_radius_m)
        and office.geofence_radius_m > 0
    )


def location_distance(qr_point, latitude, longitude) -> float | None:
    """Расстояние от присланной точки до офиса, в метрах, либо None.

    Считается и тогда, когда радиус не задан: «насколько далеко он был»
    полезно знать и без вердикта. Мусорные координаты расстояния не
    имеют — отвечать на широту 900 числом метров значило бы врать.
    """
    office = qr_point.office
    if latitude is None or longitude is None:
        return None
    if not looks_like_coordinates(latitude, longitude):
        return None
    if office is None or office.latitude is None or office.longitude is None:
        return None
    return distance_m(office.latitude, office.longitude, latitude, longitude)


def _repeated_too_soon(*, employee_id, qr_point, now) -> bool:
    """Отмечался ли человек на этой точке только что.

    Окно — `QR.STATIC_REPEAT_SECONDS`, по умолчанию минута. Считаются
    только принятые отметки: отказ «слишком далеко» не должен мешать
    подойти ближе и попробовать снова.
    """
    seconds = settings.QR.get("STATIC_REPEAT_SECONDS", 60)
    return AttendanceEvent.objects.filter(
        employee_id=employee_id,
        qr_point_id=qr_point.id,
        verification_status="ACCEPTED",
        occurred_at__gte=now - timedelta(seconds=seconds),
    ).exists()


def location_check(qr_point, latitude, longitude, accuracy_m):
    """Тот же расчёт для записи в журнал: внутри ли, если проверяли."""
    if latitude is None or longitude is None:
        return None
    if not looks_like_coordinates(latitude, longitude):
        return None
    verdict = within_office(
        qr_point.office, latitude, longitude, accuracy_m=accuracy_m
    )
    return verdict.inside if verdict.checked else None


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
