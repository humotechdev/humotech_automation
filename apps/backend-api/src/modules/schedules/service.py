"""Рабочие графики и их назначение сотрудникам.

График принадлежит организации целиком, офиса у него нет: один и тот же
режим «09:00–18:00» может действовать в разных офисах. Часовой пояс у графика
собственный — он и определяет, что считать девятью часами утра.

Назначение графика сотруднику хранится периодами в
`employee_schedule_assignments`. Периоды защищены EXCLUDE-ограничением
и не могут пересекаться, поэтому смена графика — это всегда закрытие
предыдущего периода и открытие нового, а не правка строки на месте:
иначе терялась бы история, по которой считается прошлое рабочее время.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.errors import Conflict, NotFound, ValidationFailed
from src.core.pagination import Page, paginate
from src.core.rbac import Actor, snapshot
from src.core.service import BaseService
from src.core.validation import (
    clean_text,
    validate_break,
    validate_day_interval,
    validate_timezone,
)
from src.modules.employees.models import Employee
from src.modules.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleBreak,
    ScheduleDay,
    WorkSchedule,
)
from src.modules.schedules.schemas import (
    ScheduleAssignmentView,
    ScheduleDaySpec,
    WorkScheduleCreateRequest,
    WorkScheduleDetail,
    WorkScheduleUpdateRequest,
    WorkScheduleView,
)

AUDITED_FIELDS = (
    "name", "timezone", "weekly_minutes", "late_grace_minutes",
    "early_leave_grace_minutes", "is_flexible", "status",
)


class WorkScheduleService(BaseService):
    """Требует `schedules.read` для чтения и `schedules.manage` для изменений."""

    # ------------------------------------------------------------------ чтение

    def list(
        self,
        actor: Actor,
        *,
        search: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "schedules.read")

        statement = select(WorkSchedule).where(
            WorkSchedule.organization_id == actor.organization_id
        )
        if status:
            statement = statement.where(WorkSchedule.status == status)
        if search:
            statement = statement.where(
                WorkSchedule.name.ilike(f"%{search.strip()}%")
            )

        page = paginate(
            self.session, statement, WorkSchedule, limit=limit, cursor=cursor
        )
        return Page(
            items=[WorkScheduleView.model_validate(s) for s in page.items],
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def get(self, actor: Actor, schedule_id: uuid.UUID) -> WorkScheduleDetail:
        self.access.require(actor, "schedules.read")
        self.access.require_schedule(actor, schedule_id)
        # дни и перерывы — двумя дополнительными запросами на весь график,
        # а не по запросу на каждый день
        schedule = self.session.scalar(
            select(WorkSchedule)
            .where(WorkSchedule.id == schedule_id)
            .options(selectinload(WorkSchedule.days).selectinload(ScheduleDay.breaks))
        )
        detail = WorkScheduleDetail.model_validate(schedule)
        detail.days.sort(key=lambda d: d.weekday)
        return detail

    # --------------------------------------------------------------- изменение

    def create(
        self, actor: Actor, request: WorkScheduleCreateRequest
    ) -> WorkScheduleDetail:
        self.access.require(actor, "schedules.manage")
        self._validate_days(request.days)

        schedule = WorkSchedule(
            organization_id=actor.organization_id,
            name=clean_text(request.name, field="name", required=True),
            timezone=validate_timezone(request.timezone),
            weekly_minutes=request.weekly_minutes,
            late_grace_minutes=request.late_grace_minutes,
            early_leave_grace_minutes=request.early_leave_grace_minutes,
            is_flexible=request.is_flexible,
            status="ACTIVE",
        )
        with self.atomic():
            self.session.add(schedule)
            self.flush()
            self._replace_days(schedule, request.days)
            self.flush()
            self.audit.record(
                actor, action="schedule.create", entity_type="work_schedules",
                entity_id=schedule.id, after=snapshot(schedule, AUDITED_FIELDS),
            )
        return self.get(actor, schedule.id)

    def update(
        self, actor: Actor, schedule_id: uuid.UUID,
        request: WorkScheduleUpdateRequest,
    ) -> WorkScheduleDetail:
        self.access.require(actor, "schedules.manage")
        schedule = self.access.require_schedule(actor, schedule_id)

        before = snapshot(schedule, AUDITED_FIELDS)
        changed = request.model_fields_set
        if "name" in changed and request.name is not None:
            schedule.name = clean_text(request.name, field="name", required=True)
        if "timezone" in changed and request.timezone is not None:
            schedule.timezone = validate_timezone(request.timezone)
        for field in ("weekly_minutes", "late_grace_minutes",
                      "early_leave_grace_minutes", "is_flexible"):
            if field in changed and getattr(request, field) is not None:
                setattr(schedule, field, getattr(request, field))

        if request.days is not None:
            self._validate_days(request.days)

        with self.atomic():
            if request.days is not None:
                self._replace_days(schedule, request.days)
            self.flush()
            self.audit.record(
                actor, action="schedule.update", entity_type="work_schedules",
                entity_id=schedule.id, before=before,
                after=snapshot(schedule, AUDITED_FIELDS),
            )
        return self.get(actor, schedule.id)

    def deactivate(self, actor: Actor, schedule_id: uuid.UUID) -> WorkScheduleView:
        """Выключенный график перестаёт назначаться, но уже назначенные периоды
        остаются: по ним считается прошлое рабочее время."""
        return self._set_status(
            actor, schedule_id, "INACTIVE", "schedule.deactivate"
        )

    def reactivate(self, actor: Actor, schedule_id: uuid.UUID) -> WorkScheduleView:
        return self._set_status(actor, schedule_id, "ACTIVE", "schedule.reactivate")

    def _set_status(
        self, actor: Actor, schedule_id: uuid.UUID, status: str, action: str
    ) -> WorkScheduleView:
        self.access.require(actor, "schedules.manage")
        schedule = self.access.require_schedule(actor, schedule_id)
        if schedule.status == status:
            raise Conflict(f"График уже в статусе {status}",
                           details={"status": status})
        if schedule.status == "ARCHIVED":
            raise Conflict("График в архиве, изменение статуса недоступно")

        before = snapshot(schedule, AUDITED_FIELDS)
        schedule.status = status
        with self.atomic():
            self.flush()
            self.audit.record(
                actor, action=action, entity_type="work_schedules",
                entity_id=schedule.id, before=before,
                after=snapshot(schedule, AUDITED_FIELDS),
            )
        return WorkScheduleView.model_validate(schedule)

    # ------------------------------------------------- назначение сотруднику

    def assign_to_employee(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID,
        schedule_id: uuid.UUID,
        valid_from: date,
    ) -> ScheduleAssignmentView:
        """Назначить график с указанной даты, закрыв предыдущий.

        Предыдущий период закрывается датой `valid_from - 1 день`, а не самой
        `valid_from`: границы периода в EXCLUDE-ограничении включительные
        (`daterange(..., '[]')`), и общий день считался бы пересечением.
        """
        self.access.require(actor, "schedules.manage")
        employee = self._require_employee(actor, employee_id)
        schedule = self.access.require_schedule(actor, schedule_id)

        if schedule.status != "ACTIVE":
            raise Conflict(
                "График не активен: назначать его сотрудникам нельзя",
                details={"schedule_id": str(schedule_id),
                         "status": schedule.status},
            )
        if employee.employment_status in ("TERMINATED", "ARCHIVED"):
            raise Conflict(
                "Сотрудник уволен: назначить ему график нельзя",
                details={"employment_status": employee.employment_status},
            )

        # Берётся ПОСЛЕДНИЙ период, а не действующий на дату `valid_from`.
        # Разница существенна: назначение задним числом не находит «текущий»
        # период (тот начинается позже) и без этой проверки попыталось бы
        # вставить пересекающийся — ошибку выдала бы база, а не понятное
        # сообщение о том, что назначать в прошлое нельзя.
        current = self.session.scalar(
            select(EmployeeScheduleAssignment)
            .where(EmployeeScheduleAssignment.employee_id == employee_id)
            .order_by(EmployeeScheduleAssignment.valid_from.desc())
            .limit(1)
        )
        if current is not None:
            if current.valid_from >= valid_from:
                raise ValidationFailed(
                    "Новый график должен начинаться позже начала действующего",
                    details={"valid_from": str(valid_from),
                             "current_valid_from": str(current.valid_from)},
                )
            if current.schedule_id == schedule_id and current.valid_to is None:
                raise Conflict("Этот график уже действует у сотрудника")
            if current.valid_to is not None and current.valid_to < valid_from:
                # прошлый период уже закрыт и не мешает — закрывать нечего
                current = None

        assignment = EmployeeScheduleAssignment(
            organization_id=actor.organization_id,
            employee_id=employee_id,
            schedule_id=schedule_id,
            valid_from=valid_from,
            assigned_by_user_id=actor.user_id,
        )
        with self.atomic():
            if current is not None:
                current.valid_to = valid_from - timedelta(days=1)
                # закрытие обязано дойти до базы ДО вставки нового периода:
                # ограничение проверяется в момент выполнения оператора
                self.flush()
            self.session.add(assignment)
            self.flush()
            self.audit.record(
                actor, action="employee.schedule.assign",
                entity_type="employee_schedule_assignments",
                entity_id=assignment.id,
                before=(
                    {"schedule_id": str(current.schedule_id),
                     "valid_from": str(current.valid_from)}
                    if current is not None else None
                ),
                after={"employee_id": str(employee_id),
                       "schedule_id": str(schedule_id),
                       "valid_from": str(valid_from)},
            )
        return ScheduleAssignmentView.model_validate(assignment)

    def current_assignment(
        self, employee_id: uuid.UUID, *, at: date | None = None
    ) -> EmployeeScheduleAssignment | None:
        """Действующий на дату `at` период графика сотрудника."""
        at = at or date.today()
        return self.session.scalar(
            select(EmployeeScheduleAssignment)
            .where(
                EmployeeScheduleAssignment.employee_id == employee_id,
                EmployeeScheduleAssignment.valid_from <= at,
                (EmployeeScheduleAssignment.valid_to.is_(None))
                | (EmployeeScheduleAssignment.valid_to >= at),
            )
            .order_by(EmployeeScheduleAssignment.valid_from.desc())
            .limit(1)
        )

    def history(
        self, actor: Actor, employee_id: uuid.UUID
    ) -> list[ScheduleAssignmentView]:
        self.access.require(actor, "schedules.read")
        self._require_employee(actor, employee_id)
        rows = self.session.scalars(
            select(EmployeeScheduleAssignment)
            .where(EmployeeScheduleAssignment.employee_id == employee_id)
            .order_by(EmployeeScheduleAssignment.valid_from.desc())
        )
        return [ScheduleAssignmentView.model_validate(row) for row in rows]

    # ------------------------------------------------------ внутренние правила

    def _require_employee(self, actor: Actor, employee_id: uuid.UUID) -> Employee:
        employee = self.session.get(Employee, employee_id)
        if employee is None or employee.organization_id != actor.organization_id:
            raise NotFound("Сотрудник не найден")
        return employee

    @staticmethod
    def _validate_days(days: list[ScheduleDaySpec]) -> None:
        """Интервалы рабочего времени. База такого проверить не может."""
        seen: set[int] = set()
        for day in days:
            if day.weekday in seen:
                raise ValidationFailed(
                    "День недели указан в графике дважды",
                    details={"weekday": day.weekday},
                )
            seen.add(day.weekday)
            if not 1 <= day.weekday <= 7:
                raise ValidationFailed(
                    "День недели задаётся числом от 1 (понедельник) до 7",
                    details={"weekday": day.weekday},
                )
            validate_day_interval(
                start=day.start_time, end=day.end_time,
                crosses_midnight=day.crosses_midnight,
                weekday=day.weekday, is_working_day=day.is_working_day,
            )
            if day.breaks and not day.is_working_day:
                raise ValidationFailed(
                    "У нерабочего дня не может быть перерывов",
                    details={"weekday": day.weekday},
                )
            for item in day.breaks:
                validate_break(
                    break_start=item.start_time, break_end=item.end_time,
                    day_start=day.start_time, day_end=day.end_time,
                    crosses_midnight=day.crosses_midnight,
                    weekday=day.weekday, name=item.name,
                )

    def _replace_days(
        self, schedule: WorkSchedule, days: list[ScheduleDaySpec]
    ) -> None:
        """Расписание заменяется целиком: так исключены полусохранённые правки."""
        for existing in list(schedule.days):
            self.session.delete(existing)
        self.session.flush()

        for day in days:
            row = ScheduleDay(
                schedule_id=schedule.id,
                weekday=day.weekday,
                is_working_day=day.is_working_day,
                start_time=day.start_time,
                end_time=day.end_time,
                crosses_midnight=day.crosses_midnight,
            )
            self.session.add(row)
            self.session.flush()
            for item in day.breaks:
                self.session.add(
                    ScheduleBreak(
                        schedule_day_id=row.id,
                        name=clean_text(item.name, field="break.name",
                                        required=True),
                        start_time=item.start_time,
                        end_time=item.end_time,
                        is_paid=item.is_paid,
                    )
                )
        self.session.flush()
        self.session.expire(schedule, ["days"])
