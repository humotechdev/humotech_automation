"""Ручные исправления рабочих сессий и решения по подозрительным отметкам.

Решения владельца (ТЗ 12.10):

* **Р-2.** HR с правом создаёт, закрывает и исправляет сессии. Обязательно:
  причина, старые и новые значения в журнале, кто и когда, проверка
  области, защита от одновременных изменений.
* **Р-1.** У сессии с автовыходом HR указывает фактический выход —
  тогда время становится подтверждённым.
* **Р-3.** Подозрительная (REVIEW) отметка учитывается до решения; HR
  подтверждает или аннулирует её. Аннулирование отражается во всех
  расчётах и остаётся в истории: само событие не меняется (его статус
  держит одноразовость кода), решение пишется отдельной строкой.

Защита от гонок: блокировки сотрудника в том же порядке, что у скана,
ручной отметки и автовыхода (advisory → строка сотрудника → строка
сессии), плюс сверка версии сессии (`expected_updated_at`): правка по
устаревшему экрану отклоняется с 409, а не затирает чужую.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from django.db.models import Q

from humotech.attendance.models import (
    AttendanceEvent,
    AttendanceEventReview,
    AttendanceSession,
)
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.timeframes import local_date, office_zone
from humotech.employees.models import Employee
from humotech.offices.models import Office

SESSION_FIELDS = (
    "status", "started_at", "ended_at", "duration_seconds", "unconfirmed_seconds",
    "closure_kind", "exit_event_id",
)
#: Насколько «вперёд» можно указать время — запас на расхождение часов.
FUTURE_GRACE = timedelta(minutes=15)
MIN_REASON = 3


def _now() -> datetime:
    from django.utils import timezone

    return timezone.now()


class AttendanceSessionService(BaseService):
    """Требует `attendance.correct` для правки и `attendance.manual` для создания."""

    # ------------------------------------------------------------ сессии

    def set_exit(self, actor: Actor, session_id: uuid.UUID, *, ended_at: datetime,
                 reason: str, expected_updated_at: datetime | None = None
                 ) -> AttendanceSession:
        """Фактический выход у сессии без выхода (открытой или с автовыходом)."""
        reason = self._reason(reason)
        session = self._visible_session(actor, session_id, "attendance.correct")
        ended_at = self._wall_clock(ended_at, session.office)
        with self.atomic():
            session = self._lock(session, expected_updated_at)
            if session.status not in ("OPEN", "AUTO_CLOSED"):
                raise Conflict(
                    "У сессии уже есть выход — исправьте его как время сессии",
                    details={"status": session.status, "reason": "has_exit"})
            self._check_interval(session.employee_id, session.started_at, ended_at,
                                 office=session.office, exclude=session.id)
            before = snapshot(session, SESSION_FIELDS)
            exit_event = self._manual_event(actor, session.employee_id, session.office_id,
                                            "EXIT", ended_at, reason)
            session.exit_event_id = exit_event.id
            session.ended_at = ended_at
            session.duration_seconds = int((ended_at - session.started_at).total_seconds())
            session.unconfirmed_seconds = 0
            session.status = "CORRECTED"
            session.calculated_at = _now()
            session.save()
            self._audit(actor, "attendance.session.exit_set", session, before, reason)
        return session

    def correct(self, actor: Actor, session_id: uuid.UUID, *, reason: str,
                started_at: datetime | None = None, ended_at: datetime | None = None,
                expected_updated_at: datetime | None = None) -> AttendanceSession:
        """Исправить вход и/или выход закрытой сессии."""
        reason = self._reason(reason)
        if started_at is None and ended_at is None:
            raise ValidationFailed("Укажите новое время входа или выхода",
                                   details={"fields": ["started_at", "ended_at"]})
        session = self._visible_session(actor, session_id, "attendance.correct")
        if started_at is not None:
            started_at = self._wall_clock(started_at, session.office)
        if ended_at is not None:
            ended_at = self._wall_clock(ended_at, session.office)
        with self.atomic():
            session = self._lock(session, expected_updated_at)
            if session.status not in ("CLOSED", "CORRECTED"):
                raise Conflict(
                    "Исправить можно только закрытую сессию; у сессии без выхода "
                    "сначала укажите фактический выход",
                    details={"status": session.status, "reason": "not_closed"})
            new_start = started_at or session.started_at
            new_end = ended_at or session.ended_at
            self._check_interval(session.employee_id, new_start, new_end,
                                 office=session.office, exclude=session.id)
            before = snapshot(session, SESSION_FIELDS)
            session.started_at = new_start
            session.ended_at = new_end
            session.duration_seconds = int((new_end - new_start).total_seconds())
            session.status = "CORRECTED"
            session.calculated_at = _now()
            session.save()
            self._audit(actor, "attendance.session.corrected", session, before, reason)
        return session

    def create_session(self, actor: Actor, *, employee_id: uuid.UUID, office_id: uuid.UUID,
                       started_at: datetime, ended_at: datetime, reason: str
                       ) -> AttendanceSession:
        """Сессия за прошедший интервал, которого нет в отметках (сканер не работал)."""
        from humotech.attendance.hr import AttendanceHrService

        reason = self._reason(reason)
        self.access.require(actor, "attendance.manual")
        self.access.require(actor, "attendance.correct")
        hr = AttendanceHrService()
        hr._require_employee_visible(actor, employee_id, "attendance.manual")
        self.access.require_office(actor, office_id, "attendance.manual")
        hr._forbid_self_attendance(actor, employee_id)
        office = Office.objects.select_related("organization").filter(
            id=office_id, organization_id=actor.organization_id).first()
        if office is None:
            raise NotFound("Офис не найден")
        started_at = self._wall_clock(started_at, office)
        ended_at = self._wall_clock(ended_at, office)
        with self.atomic():
            self._lock_employee(employee_id)
            self._check_interval(employee_id, started_at, ended_at, office=office)
            entry = self._manual_event(actor, employee_id, office_id, "ENTRY", started_at, reason)
            exit_event = self._manual_event(actor, employee_id, office_id, "EXIT", ended_at, reason)
            session = AttendanceSession.objects.create(
                organization_id=actor.organization_id, employee_id=employee_id,
                office_id=office_id, entry_event_id=entry.id, exit_event_id=exit_event.id,
                started_at=started_at, ended_at=ended_at,
                duration_seconds=int((ended_at - started_at).total_seconds()),
                status="CORRECTED", calculated_at=_now(),
            )
            self._audit(actor, "attendance.session.created", session, None, reason)
        return session

    # --------------------------------------------------- подозрительные отметки

    def review_event(self, actor: Actor, event_id: uuid.UUID, *, decision: str,
                     reason: str) -> AttendanceEventReview:
        """Подтвердить или аннулировать подозрительную (REVIEW) отметку."""
        from humotech.attendance.hr import AttendanceHrService

        if decision not in ("CONFIRM", "ANNUL"):
            raise ValidationFailed("Решение — CONFIRM или ANNUL",
                                   details={"decision": decision})
        reason = self._reason(reason)
        self.access.require(actor, "attendance.correct")
        event = AttendanceEvent.objects.filter(
            id=event_id, organization_id=actor.organization_id).first()
        if event is None:
            raise NotFound("Отметка не найдена")
        hr = AttendanceHrService()
        hr._require_employee_visible(actor, event.employee_id, "attendance.correct")
        self.access.require_office(actor, event.office_id, "attendance.correct")
        hr._forbid_self_attendance(actor, event.employee_id)

        with self.atomic():
            self._lock_employee(event.employee_id)
            if event.verification_status != "REVIEW":
                raise Conflict("Решение принимается только по отметке «на проверке»",
                               details={"verification_status": event.verification_status})
            if AttendanceEventReview.objects.filter(event_id=event.id).exists():
                raise Conflict("По этой отметке решение уже принято",
                               details={"reason": "already_reviewed"})
            review = AttendanceEventReview.objects.create(
                organization_id=event.organization_id, event_id=event.id,
                decision=decision, reason=reason, decided_by_user_id=actor.user_id,
                decided_at=_now(),
            )
            session_change = None
            if decision == "ANNUL":
                session_change = self._annul(event)
            self.audit.record(
                actor, action="attendance.event.reviewed", entity_type="attendance_events",
                entity_id=event.id, before={"verification_status": "REVIEW", "decision": None},
                after={"decision": decision, "reason": reason, "session": session_change},
            )
        return review

    def _annul(self, event: AttendanceEvent) -> dict | None:
        """Аннулированный вход делает сессию недействительной; аннулированный
        выход оставляет сессию без подтверждённого выхода — как автовыход."""
        session = (AttendanceSession.objects.select_for_update(of=("self",))
                   .filter(Q(entry_event_id=event.id) | Q(exit_event_id=event.id)).first())
        if session is None:
            return None
        before = snapshot(session, SESSION_FIELDS)
        if session.entry_event_id == event.id:
            session.status = "INVALID"
        elif session.ended_at is not None:
            session.status = "AUTO_CLOSED"
            session.closure_kind = "EXIT_ANNULLED"
            session.unconfirmed_seconds = max(
                int((session.ended_at - session.started_at).total_seconds()), 0)
            session.duration_seconds = 0
        session.calculated_at = _now()
        session.save()
        return {"session_id": str(session.id), "before": before,
                "after": snapshot(session, SESSION_FIELDS)}

    # ------------------------------------------------------------ внутреннее

    @staticmethod
    def _reason(reason: str | None) -> str:
        reason = (reason or "").strip()
        if len(reason) < MIN_REASON:
            raise ValidationFailed("Причина исправления обязательна",
                                   details={"field": "reason"})
        return reason[:1000]

    @staticmethod
    def _wall_clock(value: datetime, office) -> datetime:
        """Время без смещения — «время офиса», как его вводит CRM."""
        if value.tzinfo is None:
            return value.replace(tzinfo=office_zone(office))
        return value

    def _visible_session(self, actor: Actor, session_id, permission: str) -> AttendanceSession:
        from humotech.attendance.hr import AttendanceHrService

        self.access.require(actor, permission)
        session = (AttendanceSession.objects.select_related("office", "office__organization")
                   .filter(id=session_id, organization_id=actor.organization_id).first())
        if session is None:
            raise NotFound("Сессия не найдена")
        hr = AttendanceHrService()
        # Чужой сотрудник отвечает «не найдено», а не «нельзя».
        hr._require_employee_visible(actor, session.employee_id, permission)
        self.access.require_office(actor, session.office_id, permission)
        hr._forbid_self_attendance(actor, session.employee_id)
        return session

    @staticmethod
    def _lock_employee(employee_id) -> None:
        from humotech.absences.services import lock_employee

        lock_employee(employee_id)
        Employee.objects.select_for_update().filter(id=employee_id).first()

    def _lock(self, session: AttendanceSession, expected_updated_at) -> AttendanceSession:
        self._lock_employee(session.employee_id)
        fresh = (AttendanceSession.objects.select_for_update(of=("self",))
                 .select_related("office", "office__organization").get(id=session.id))
        if expected_updated_at is not None and fresh.updated_at != expected_updated_at:
            raise Conflict(
                "Сессию уже изменили — обновите страницу и проверьте значения",
                details={"reason": "stale_version",
                         "updated_at": fresh.updated_at.isoformat()})
        return fresh

    @staticmethod
    def _check_interval(employee_id, start: datetime, end: datetime, *, office,
                        exclude=None) -> None:
        from humotech.attendance.field_work import require_no_field_work

        if end <= start:
            raise ValidationFailed("Выход должен быть позже входа",
                                   details={"field": "ended_at"})
        if end > _now() + FUTURE_GRACE:
            raise ValidationFailed("Время выхода не может быть в будущем",
                                   details={"field": "ended_at"})
        tz = office_zone(office)
        if local_date(start, tz) != local_date(end - timedelta(microseconds=1), tz):
            # Ночные смены пока не поддерживаются (решение Р-9): сессия
            # принадлежит одному дню офиса.
            raise ValidationFailed("Сессия должна укладываться в один день офиса",
                                   details={"reason": "crosses_midnight"})
        overlap = (AttendanceSession.objects.filter(employee_id=employee_id)
                   .exclude(status="INVALID")
                   .filter(started_at__lt=end)
                   .filter(Q(ended_at__gt=start) | Q(ended_at__isnull=True)))
        if exclude is not None:
            overlap = overlap.exclude(id=exclude)
        if overlap.exists():
            raise Conflict("Интервал пересекается с другой сессией сотрудника",
                           details={"reason": "overlap"})
        day = local_date(start, tz)
        require_no_field_work(employee_id, day, day)

    @staticmethod
    def _manual_event(actor, employee_id, office_id, event_type, at, reason) -> AttendanceEvent:
        return AttendanceEvent.objects.create(
            organization_id=actor.organization_id, employee_id=employee_id,
            office_id=office_id, event_type=event_type, source="MANUAL",
            verification_status="ACCEPTED", occurred_at=at,
            event_metadata={"reason": reason, "created_by_user_id": str(actor.user_id)},
        )

    def _audit(self, actor, action, session, before, reason) -> None:
        self.audit.record(
            actor, action=action, entity_type="attendance_sessions", entity_id=session.id,
            before=before, after={**snapshot(session, SESSION_FIELDS), "reason": reason},
        )
