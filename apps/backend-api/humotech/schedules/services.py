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
from dataclasses import dataclass, field
from datetime import date, time, timedelta

from django.db.models import Q

from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import (
    clean_text,
    validate_break,
    validate_day_interval,
    validate_timezone,
)
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleBreak,
    ScheduleDay,
    WorkSchedule,
)

AUDITED_FIELDS = (
    "name", "timezone", "weekly_minutes", "late_grace_minutes",
    "early_leave_grace_minutes", "is_flexible", "status",
)


@dataclass(frozen=True)
class BreakSpec:
    """Перерыв внутри рабочего дня. Изолированный DTO, к ORM не привязан."""

    name: str
    start_time: time
    end_time: time
    is_paid: bool = False


@dataclass(frozen=True)
class DaySpec:
    """День недели по ISO-8601: 1 — понедельник, 7 — воскресенье."""

    weekday: int
    is_working_day: bool
    start_time: time | None = None
    end_time: time | None = None
    crosses_midnight: bool = False
    breaks: tuple[BreakSpec, ...] = field(default_factory=tuple)


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

        queryset = WorkSchedule.objects.filter(
            organization_id=actor.organization_id
        )
        if status:
            queryset = queryset.filter(status=status)
        if search:
            queryset = queryset.filter(name__icontains=search.strip())
        return paginate(queryset, limit=limit, cursor=cursor)

    def get(self, actor: Actor, schedule_id: uuid.UUID) -> WorkSchedule:
        self.access.require(actor, "schedules.read")
        return self._detail(actor, schedule_id)

    def _detail(self, actor: Actor, schedule_id: uuid.UUID) -> WorkSchedule:
        """Карточка графика без проверки `schedules.read`: создание и правка
        возвращают результат, и требовать для этого право на чтение нельзя."""
        self.access.require_schedule(actor, schedule_id)
        # дни и перерывы — двумя дополнительными запросами на весь график,
        # а не по запросу на каждый день
        return (
            WorkSchedule.objects.prefetch_related("days__breaks")
            .get(id=schedule_id)
        )

    def days_of(self, schedule: WorkSchedule) -> list[ScheduleDay]:
        return sorted(schedule.days.all(), key=lambda day: day.weekday)

    # --------------------------------------------------------------- изменение

    def create(
        self,
        actor: Actor,
        *,
        name: str,
        timezone: str,
        weekly_minutes: int,
        late_grace_minutes: int = 0,
        early_leave_grace_minutes: int = 0,
        is_flexible: bool = False,
        days: list[DaySpec] | None = None,
    ) -> WorkSchedule:
        self.access.require(actor, "schedules.manage")
        days = days or []
        self._validate_days(days)

        with self.atomic():
            schedule = WorkSchedule.objects.create(
                organization_id=actor.organization_id,
                name=clean_text(name, field="name", required=True),
                timezone=validate_timezone(timezone),
                weekly_minutes=weekly_minutes,
                late_grace_minutes=late_grace_minutes,
                early_leave_grace_minutes=early_leave_grace_minutes,
                is_flexible=is_flexible,
                status="ACTIVE",
            )
            self._replace_days(schedule, days)
            self.audit.record(
                actor, action="schedule.create", entity_type="work_schedules",
                entity_id=schedule.id, after=snapshot(schedule, AUDITED_FIELDS),
            )
        return self._detail(actor, schedule.id)

    def update(
        self, actor: Actor, schedule_id: uuid.UUID, *,
        days: list[DaySpec] | None = None, **changes,
    ) -> WorkSchedule:
        """Переданный список дней заменяет расписание ЦЕЛИКОМ.

        Частичная правка отдельных дней ввела бы неочевидную семантику
        слияния: непонятно, что означает отсутствие дня в списке.
        """
        self.access.require(actor, "schedules.manage")
        schedule = self.access.require_schedule(actor, schedule_id)

        before = snapshot(schedule, AUDITED_FIELDS)
        if changes.get("name") is not None:
            schedule.name = clean_text(changes["name"], field="name", required=True)
        if changes.get("timezone") is not None:
            schedule.timezone = validate_timezone(changes["timezone"])
        for field_name in ("weekly_minutes", "late_grace_minutes",
                           "early_leave_grace_minutes", "is_flexible"):
            if changes.get(field_name) is not None:
                setattr(schedule, field_name, changes[field_name])

        if days is not None:
            self._validate_days(days)

        with self.atomic():
            schedule.save()
            if days is not None:
                self._replace_days(schedule, days)
            self.audit.record(
                actor, action="schedule.update", entity_type="work_schedules",
                entity_id=schedule.id, before=before,
                after=snapshot(schedule, AUDITED_FIELDS),
            )
        return self._detail(actor, schedule.id)

    def deactivate(self, actor: Actor, schedule_id: uuid.UUID) -> WorkSchedule:
        """Выключенный график перестаёт назначаться, но уже назначенные периоды
        остаются: по ним считается прошлое рабочее время."""
        return self._set_status(
            actor, schedule_id, "INACTIVE", "schedule.deactivate"
        )

    def reactivate(self, actor: Actor, schedule_id: uuid.UUID) -> WorkSchedule:
        return self._set_status(actor, schedule_id, "ACTIVE", "schedule.reactivate")

    def _set_status(
        self, actor: Actor, schedule_id: uuid.UUID, status: str, action: str
    ) -> WorkSchedule:
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
            schedule.save()
            self.audit.record(
                actor, action=action, entity_type="work_schedules",
                entity_id=schedule.id, before=before,
                after=snapshot(schedule, AUDITED_FIELDS),
            )
        return schedule

    # ------------------------------------------------- назначение сотруднику

    def delete(self, actor: Actor, schedule_id: uuid.UUID) -> None:
        """Убрать график совсем — только пока его никому не назначали.

        Назначенный график остаётся навсегда: по нему считается
        отработанное время прошлых месяцев. Такой график выключают.
        """
        self.access.require(actor, "schedules.manage")
        schedule = self.access.require_schedule(actor, schedule_id)

        used = EmployeeScheduleAssignment.objects.filter(
            schedule_id=schedule.id
        ).count()
        if used:
            raise Conflict(
                f"График уже назначен сотрудникам ({used}). Его можно только "
                f"выключить: по нему считается отработанное время прошлых "
                f"месяцев",
                details={"schedule_id": str(schedule.id), "used": used},
            )

        with self.atomic():
            self.audit.record(
                actor, action="schedule.delete", entity_type="work_schedules",
                entity_id=schedule.id, before=snapshot(schedule, AUDITED_FIELDS),
                after=None,
            )
            # Дни и перерывы уходят вместе с графиком: они его часть, а не
            # самостоятельные записи, и ссылаются только на него.
            schedule.delete()

    def assign_to_employee(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID,
        schedule_id: uuid.UUID,
        valid_from: date,
    ) -> EmployeeScheduleAssignment:
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
        current = (
            EmployeeScheduleAssignment.objects.filter(employee_id=employee_id)
            .order_by("-valid_from")
            .first()
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

        with self.atomic():
            if current is not None:
                current.valid_to = valid_from - timedelta(days=1)
                # закрытие обязано дойти до базы ДО вставки нового периода:
                # ограничение проверяется в момент выполнения оператора
                current.save(update_fields=["valid_to", "updated_at"])
            assignment = EmployeeScheduleAssignment.objects.create(
                organization_id=actor.organization_id,
                employee_id=employee_id,
                schedule_id=schedule_id,
                valid_from=valid_from,
                assigned_by_user_id=actor.user_id,
            )
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
        return assignment

    def assign_to_department(
        self,
        actor: Actor,
        *,
        department_id: uuid.UUID,
        schedule_id: uuid.UUID,
        valid_from: date,
    ) -> dict:
        """Назначить график всем, кто числится в отделе СЕЙЧАС.

        Это снимок состава, а не правило «у отдела такой график». Разница
        видна при переводе: человек, пришедший в отдел завтра, графика от
        этого назначения не получит, а ушедший из отдела не потеряет тот,
        по которому уже работает. Правило «график следует за отделом»
        меняло бы прошлое при каждом переводе — и табель за прошлый месяц
        переставал бы сходиться сам собой.

        Отказ по одному человеку не отменяет остальных: у кого-то график
        уже назначен с той же даты, и это не повод не назначить его всем
        прочим. Кто не получил и почему — в ответе.
        """
        self.access.require(actor, "schedules.manage")

        today_staff = list(
            EmployeeAssignment.objects.filter(
                department_id=department_id, organization_id=actor.organization_id
            )
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=valid_from))
            .values_list("employee_id", flat=True)
            .distinct()
        )
        if not today_staff:
            raise Conflict(
                "В отделе нет сотрудников: назначать график некому",
                details={"department_id": str(department_id)},
            )

        assigned: list[str] = []
        skipped: list[dict] = []
        for employee_id in today_staff:
            try:
                self.assign_to_employee(
                    actor,
                    employee_id=employee_id,
                    schedule_id=schedule_id,
                    valid_from=valid_from,
                )
                assigned.append(str(employee_id))
            except (Conflict, ValidationFailed) as exc:
                skipped.append({"employee_id": str(employee_id),
                                "reason": str(exc)})
        return {"assigned": assigned, "skipped": skipped}

    def current_assignment(
        self, employee_id: uuid.UUID, *, at: date | None = None
    ) -> EmployeeScheduleAssignment | None:
        """Действующий на дату `at` период графика сотрудника."""
        from django.db.models import Q

        moment = at or date.today()
        return (
            EmployeeScheduleAssignment.objects.filter(employee_id=employee_id)
            .filter(valid_from__lte=moment)
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=moment))
            .order_by("-valid_from")
            .first()
        )

    def history(
        self, actor: Actor, employee_id: uuid.UUID
    ) -> list[EmployeeScheduleAssignment]:
        self.access.require(actor, "schedules.read")
        self._require_employee(actor, employee_id)
        return list(
            EmployeeScheduleAssignment.objects.filter(employee_id=employee_id)
            .select_related("schedule")
            .order_by("-valid_from")
        )

    # ------------------------------------------------------ внутренние правила

    def _require_employee(self, actor: Actor, employee_id: uuid.UUID) -> Employee:
        employee = Employee.objects.filter(
            id=employee_id, organization_id=actor.organization_id
        ).first()
        if employee is None:
            raise NotFound("Сотрудник не найден")
        return employee

    @staticmethod
    def _validate_days(days: list[DaySpec]) -> None:
        """Интервалы рабочего времени. База такого проверить не может:
        ночная смена (22:00–06:00) для CHECK неотличима от опечатки."""
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

    def _replace_days(self, schedule: WorkSchedule, days: list[DaySpec]) -> None:
        """Расписание заменяется целиком: так исключены полусохранённые правки.

        Перерывы удаляются каскадом вместе с днями — это задано схемой
        (`ON DELETE CASCADE`), а не поведением ORM.
        """
        schedule.days.all().delete()
        for day in days:
            row = ScheduleDay.objects.create(
                schedule=schedule,
                weekday=day.weekday,
                is_working_day=day.is_working_day,
                start_time=day.start_time,
                end_time=day.end_time,
                crosses_midnight=day.crosses_midnight,
            )
            for item in day.breaks:
                ScheduleBreak.objects.create(
                    schedule_day=row,
                    name=clean_text(item.name, field="break.name", required=True),
                    start_time=item.start_time,
                    end_time=item.end_time,
                    is_paid=item.is_paid,
                )
