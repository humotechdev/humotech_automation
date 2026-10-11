"""Автовыход: вход без выхода закрывается в полночь по поясу офиса.

Решение владельца Р-1 (ТЗ 12.10). Сотрудник отметил вход, а выход забыл.
В 00:00 следующего календарного дня по поясу офиса сессия закрывается:

* статус `AUTO_CLOSED`, причина `AUTO_MIDNIGHT`, `auto_closed_at` — когда
  закрыла система;
* `ended_at` — технический момент закрытия (полночь), а **не** фактический
  выход: событие выхода не создаётся, выход не выдумывается;
* подтверждённого времени нет (`duration_seconds = 0`); время от входа до
  полуночи — **неподтверждённое** (`unconfirmed_seconds`), его видно отдельно;
* HR уточняет фактический выход с причиной (`sessions_hr.set_exit`).

Устойчивость:

* **Повторный запуск** ничего не меняет: закрываются только сессии в
  статусе OPEN, и проверка повторяется под блокировкой строки.
* **Пропущенный запуск** догоняется: берутся все открытые сессии, чей день
  уже кончился, сколько бы суток ни прошло.
* **Одновременная ручная отметка или скан**: перед закрытием берутся те же
  блокировки сотрудника, что у скана и ручной отметки (advisory + строка).
* **Пояс и переход времени**: полночь считается в поясе офиса сессии через
  `zoneinfo`, сутки длиной 23 или 25 часов учитываются.
* **Скан наутро** на точке «вход и выход» сначала закрывает вчерашнюю сессию
  автовыходом (`close_for_employee`), иначе он стал бы выходом длиной в сутки.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from humotech.attendance.models import AttendanceSession
from humotech.core.timeframes import day_bounds, local_date, office_zone

logger = logging.getLogger(__name__)

ACTION = "attendance.session.auto_closed"
FIELDS = ("status", "ended_at", "duration_seconds", "unconfirmed_seconds",
          "closure_kind", "auto_closed_at")


def midnight_after(session: AttendanceSession) -> datetime:
    """Начало суток, следующих за днём входа, в поясе офиса сессии."""
    tz = office_zone(session.office)
    return day_bounds(local_date(session.started_at, tz), tz)[1]


def _overdue(sessions, now: datetime):
    for session in sessions:
        if now >= midnight_after(session):
            yield session


def close_overdue_sessions(*, now: datetime | None = None, dry_run: bool = False,
                           employee_id: uuid.UUID | None = None) -> int:
    """Закрыть автовыходом все открытые сессии, чей день уже кончился.

    Возвращает число закрытых (при `dry_run` — число тех, что были бы закрыты).
    """
    moment = now or timezone.now()
    candidates = AttendanceSession.objects.filter(status="OPEN", started_at__lt=moment)
    if employee_id is not None:
        candidates = candidates.filter(employee_id=employee_id)
    candidates = candidates.select_related("office", "office__organization")
    due = list(_overdue(candidates, moment))
    if dry_run:
        return len(due)
    closed = 0
    for session in due:
        try:
            with transaction.atomic():
                closed += _close_one(session.id, session.employee_id, moment)
        except Exception:  # noqa: BLE001 — одна сессия не должна останавливать остальные
            logger.exception("auto-close failed for session %s", session.id)
    return closed


def close_for_employee(employee_id: uuid.UUID, now: datetime) -> int:
    """Ленивый автовыход одного сотрудника — внутри уже открытой транзакции
    и под уже взятой блокировкой сотрудника (вызывается сканом)."""
    rows = (
        AttendanceSession.objects.select_for_update(of=("self",))
        .filter(employee_id=employee_id, status="OPEN", started_at__lt=now)
        .select_related("office", "office__organization")
    )
    count = 0
    for session in _overdue(rows, now):
        _apply(session, now)
        count += 1
    return count


def _close_one(session_id, employee_id, now: datetime) -> int:
    from humotech.absences.services import lock_employee
    from humotech.employees.models import Employee

    # Те же блокировки и в том же порядке, что у скана и ручной отметки.
    lock_employee(employee_id)
    Employee.objects.select_for_update().filter(id=employee_id).first()
    session = (
        AttendanceSession.objects.select_for_update(of=("self",))
        .select_related("office", "office__organization")
        .filter(id=session_id, status="OPEN")
        .first()
    )
    if session is None or now < midnight_after(session):
        return 0  # уже закрыта сканом, вручную или другим запуском
    _apply(session, now)
    return 1


def _apply(session: AttendanceSession, now: datetime) -> None:
    from humotech.core.rbac import AuditTrail, snapshot

    before = snapshot(session, FIELDS)
    boundary = midnight_after(session)
    session.status = "AUTO_CLOSED"
    session.closure_kind = "AUTO_MIDNIGHT"
    session.ended_at = boundary
    session.duration_seconds = 0
    session.unconfirmed_seconds = max(int((boundary - session.started_at).total_seconds()), 0)
    session.auto_closed_at = now
    session.calculated_at = now
    session.save(update_fields=[*FIELDS, "calculated_at", "updated_at"])
    AuditTrail().record_system(
        organization_id=session.organization_id,
        action=ACTION,
        entity_type="attendance_sessions",
        entity_id=session.id,
        before=before,
        after=snapshot(session, FIELDS),
    )
