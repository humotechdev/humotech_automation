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
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from humotech.employees.models import (
    Employee,
    EmployeeAssignment,
    EmployeeOfficeAccess,
)
from humotech.attendance.models import AttendanceEvent, AttendanceSession, FieldWorkRequest
from humotech.core.timeframes import office_zone
from humotech.offices.geo import (
    Decision,
    distance_m,
    geofence_kind,
    looks_like_coordinates,
    within_office,
)
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
    # Погрешность пересекает границу контура: человек может быть и
    # внутри, и снаружи. Ни принять, ни отказать «слишком далеко» честно
    # нельзя — нужна новая точка.
    LOCATION_AMBIGUOUS = "LOCATION_AMBIGUOUS"
    # Точка получена слишком давно: это место, где человек БЫЛ.
    LOCATION_STALE = "LOCATION_STALE"
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
    # День уже засчитан как подтверждённая выездная работа: физическая
    # отметка поверх полной нормы дала бы двойной зачёт.
    FIELD_WORK_CONFIRMED = "FIELD_WORK_CONFIRMED"


@dataclass(frozen=True)
class LocationAssessment:
    """Одно решение по присланному месту — для отказа и для журнала сразу.

    `reason` — причина отказа по месту, либо None. `inside` — что записать
    в журнал: да, нет или «не установлено». `distance_m` у круга — до
    центра офиса, у контура — до его границы.
    """

    reason: str | None = None
    inside: bool | None = None
    distance_m: float | None = None
    kind: str | None = None


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
    location_age_s: Decimal | None = None,
    location_source: str | None = None,
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
            location_age_s=location_age_s,
            location_source=location_source,
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
    location_age_s: Decimal | None,
    location_source: str | None,
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

    # Same employee lock as HR request and Telegram decision. Do not count
    # physical QR time on top of an already credited full field-work shift.
    active_field_work = list(FieldWorkRequest.objects.select_related("office", "office__organization")
        .filter(employee_id=employee_id, status__in=("PENDING", "CONFIRMED"),
                date__gte=occurred_at.date() - timedelta(days=1),
                date__lte=occurred_at.date() + timedelta(days=1)))
    matching_field_work = [row for row in active_field_work
        if occurred_at.astimezone(office_zone(row.office)).date() == row.date]
    field_work = any(row.status == "CONFIRMED" for row in matching_field_work)

    current_session = open_session_for(employee_id=employee_id)

    # направление: у точки ENTRY/EXIT оно фиксировано, у BOTH — по факту
    if qr_point.direction_mode in ("ENTRY", "EXIT"):
        event_type = qr_point.direction_mode
    else:
        event_type = "EXIT" if current_session is not None else "ENTRY"

    # Считается здесь и только здесь, одним вызовом: и причина отказа, и
    # то, что пишется в журнал, берутся из ОДНОГО решения. Готового
    # «внутри» вызывающий передать не может — такого параметра нет.
    location = assess_location(
        qr_point, latitude, longitude, location_accuracy_m, location_age_s,
        strict_location=strict_location,
    )

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
        location=location,
        inside_office_network=inside_office_network,
        current_session=current_session,
        strict_location=strict_location,
    )
    if field_work and reason is None:
        reason = RejectionReason.FIELD_WORK_CONFIRMED
    accepted = reason is None

    if accepted:
        pending = [row for row in matching_field_work if row.status == "PENDING"]
        for request in pending:
            request.status = "CANCELLED"
            request.cancelled_at = now
            request.save(update_fields=["status", "cancelled_at", "updated_at"])
            from humotech.notifications.models import Notification
            Notification.objects.filter(related_entity_type="attendance_field_work_request",
                related_entity_id=request.id, status="PENDING").update(status="CANCELLED")
            from humotech.audit.models import AuditLog
            AuditLog.objects.create(organization_id=request.organization_id,
                actor_employee_id=employee_id, action="field_work.cancelled_by_qr",
                entity_type="attendance_field_work_requests", entity_id=request.id,
                new_values={"date": request.date.isoformat(), "status": "CANCELLED"})

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
        inside_geofence=location.inside,
        distance_m=(
            Decimal(str(round(location.distance_m, 2)))
            if location.distance_m is not None else None
        ),
        inside_office_network=inside_office_network,
        client_event_id=client_event_id,
        rejection_reason=reason,
        event_metadata=_location_metadata(
            location, location_age_s=location_age_s,
            location_source=location_source,
            anomalies=(
                _anomalies(
                    employee_id=employee_id, qr_nonce_hash=qr_nonce_hash,
                    ip_address=ip_address, latitude=latitude,
                    longitude=longitude, accuracy_m=location_accuracy_m,
                    inside_office_network=inside_office_network,
                )
                if accepted else ()
            ),
        ),
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
    location: LocationAssessment,
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
    if location.reason is not None:
        return location.reason

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


def assess_location(
    qr_point, latitude, longitude, accuracy_m, age_s=None, *, strict_location=False
) -> LocationAssessment:
    """Решение по месту. ЕДИНСТВЕННОЕ место, где оно принимается.

    Порядок: сперва похоже ли это вообще на точку на Земле, потом не
    устарела ли она и можно ли верить погрешности, и только потом — где
    человек относительно геозоны. Считать расстояние от мусора
    бессмысленно, а отвечать «слишком далеко» на широту 900 — врать про
    причину.

    Офис без геозоны проверку не проходит и не заваливает: сравнивать не
    с чем, и записывать это человеку в вину нельзя. `inside` в таком
    случае остаётся `None` — «не проверялось», а не «нарушение», — а
    расстояние до точки офиса, если она есть, всё равно пишется:
    «насколько далеко он был» полезно знать и без вердикта.
    """
    if latitude is None or longitude is None:
        return LocationAssessment()
    if not looks_like_coordinates(latitude, longitude):
        return LocationAssessment(reason=RejectionReason.LOCATION_TOO_VAGUE)

    office = qr_point.office
    ceiling = settings.QR["MAX_LOCATION_ACCURACY_M"]
    # Потолок точки может только ужесточить общий, но не ослабить:
    # иначе настройка в карточке точки была бы видимостью.
    own = getattr(qr_point, "allowed_location_accuracy_m", None)
    if own and own > 0:
        ceiling = min(ceiling, own)

    reason = None
    if age_s is not None and _stale(age_s):
        reason = RejectionReason.LOCATION_STALE

    accuracy = None
    if accuracy_m is not None:
        try:
            accuracy = Decimal(str(accuracy_m))
        except (ArithmeticError, TypeError, ValueError):
            accuracy = None
            reason = reason or RejectionReason.LOCATION_TOO_VAGUE
        else:
            if not accuracy.is_finite() or accuracy <= 0 or accuracy > ceiling:
                accuracy = None
                reason = reason or RejectionReason.LOCATION_TOO_VAGUE

    # Для печатного кода (strict_location) круг с неизвестной погрешностью
    # означал бы нулевой допуск (offices/geo.py): булавка, выбранная на
    # карте в центре офиса, прошла бы как «внутри» из дома. Телефон,
    # измеривший координату, погрешность присылает; её отсутствие —
    # признак выбранной точки. Для меняющегося кода (Mini App) поведение
    # прежнее: сканер отдаёт настоящую погрешность прибора.
    if (strict_location and reason is None and accuracy is None
            and geofence_kind(office) == "CIRCLE"):
        reason = RejectionReason.LOCATION_TOO_VAGUE
    verdict = within_office(
        office, latitude, longitude, accuracy_m=accuracy,
        unknown_accuracy_m=ceiling,
    )
    if not verdict.checked:
        near = None
        if (
            office is not None
            and office.latitude is not None
            and office.longitude is not None
        ):
            near = distance_m(office.latitude, office.longitude, latitude, longitude)
        return LocationAssessment(reason=reason, distance_m=near)

    if reason is None and verdict.decision == Decision.OUTSIDE:
        reason = RejectionReason.OUTSIDE_GEOFENCE
    elif reason is None and verdict.decision == Decision.AMBIGUOUS:
        reason = RejectionReason.LOCATION_AMBIGUOUS
    return LocationAssessment(
        reason=reason,
        # Негодной или устаревшей точке «внутри» не ставится: принять её
        # нельзя, а «был внутри» в журнале читалось бы как подтверждение.
        inside=(
            verdict.inside
            if reason in (None, RejectionReason.OUTSIDE_GEOFENCE)
            else None
        ),
        distance_m=verdict.distance_m,
        kind=verdict.kind,
    )


def _stale(age_s) -> bool:
    """Старше ли точка допустимого. Мусор вместо возраста — тоже старая."""
    limit = settings.QR.get("MAX_LOCATION_AGE_SECONDS", 30)
    try:
        age = Decimal(str(age_s))
    except (ArithmeticError, TypeError, ValueError):
        return True
    return not age.is_finite() or age < 0 or age > limit


def _location_metadata(location, *, location_age_s, location_source, anomalies=()):
    """Как получена точка — для разбора спорной отметки, не для решения.

    Источник и возраст присылает клиент, и доказательством они не
    являются. Но без них на вопрос «почему телефон видел человека в
    двухстах метрах» нечего ответить, кроме догадки.
    """
    data = {}
    if location.kind is not None:
        data["geofence_kind"] = location.kind
    if location_source:
        data["location_source"] = str(location_source)[:20]
    if location_age_s is not None:
        try:
            data["location_age_s"] = float(location_age_s)
        except (TypeError, ValueError):
            pass
    if anomalies:
        data["anomalies"] = list(anomalies)
    return data or None


# Точнее этого телефон в реальной жизни почти не отвечает: в помещении
# погрешность — десятки метров, на улице — от трёх-пяти. Ровные «1 м» —
# частый почерк подменённой геолокации.
SUSPICIOUS_ACCURACY_M = Decimal("2")


class Anomaly:
    """Почему принятая отметка подсвечена HR. Отметку это не отменяет."""

    # Ни координат, ни подтверждённой офисной сети: место не проверялось.
    NO_LOCATION = "NO_LOCATION"
    # Погрешность подозрительно мала для телефона.
    PERFECT_ACCURACY = "PERFECT_ACCURACY"
    # Тот же сменный код уже засчитан другому сотруднику с другого адреса:
    # похоже, код сфотографировали и переслали.
    SHARED_CODE = "SHARED_CODE"


def _anomalies(
    *, employee_id, qr_nonce_hash, ip_address, latitude, longitude, accuracy_m,
    inside_office_network=None,
) -> tuple[str, ...]:
    """Признаки, по которым HR стоит взглянуть на принятую отметку.

    Ни один из них не доказывает обман — у каждого бывает честное
    объяснение, — поэтому отметку они не отклоняют, а только помечают.
    Решение остаётся за человеком, который видит журнал целиком.
    """
    found = []
    if latitude is None or longitude is None:
        # Офисная сеть — тоже проверка места: экран внутри офиса без
        # геолокации иначе подсвечивал бы каждую отметку.
        if inside_office_network is not True:
            found.append(Anomaly.NO_LOCATION)
    elif accuracy_m is not None:
        try:
            if Decimal(str(accuracy_m)) < SUSPICIOUS_ACCURACY_M:
                found.append(Anomaly.PERFECT_ACCURACY)
        except (InvalidOperation, ValueError):
            pass
    if qr_nonce_hash is not None and ip_address:
        shared = (
            AttendanceEvent.objects.filter(
                qr_nonce_hash=qr_nonce_hash, verification_status="ACCEPTED",
            )
            .exclude(employee_id=employee_id)
            .exclude(ip_address__isnull=True)
            .exclude(ip_address=ip_address)
            .exists()
        )
        if shared:
            found.append(Anomaly.SHARED_CODE)
    return tuple(found)


def office_is_located(office) -> bool:
    """Есть ли у офиса геозона — круг или контур, — с которой сравнивать."""
    return geofence_kind(office) is not None


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


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
