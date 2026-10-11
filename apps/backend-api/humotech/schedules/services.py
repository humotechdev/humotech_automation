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

from humotech.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import (
    clean_text,
    validate_break,
    validate_day_interval,
    validate_timezone,
)
from humotech.departments.models import Department
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.selectors import require_visible_employee
from humotech.offices.models import Office
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
#: Поля, которые принадлежат ВЕРСИИ графика: их правка меняет расчёт дней.
#: Остальные (название, пояс, статус, регион) — шапка графика, без истории.
VERSIONED_FIELDS = (
    "weekly_minutes", "late_grace_minutes", "early_leave_grace_minutes", "is_flexible",
)


def days_snapshot(days) -> list[dict]:
    """Дни и перерывы версии для журнала аудита: раньше они в него не
    попадали, и прежнее расписание нельзя было восстановить."""
    return [
        {
            "weekday": day.weekday,
            "is_working_day": day.is_working_day,
            "start_time": str(day.start_time) if day.start_time else None,
            "end_time": str(day.end_time) if day.end_time else None,
            "breaks": [
                {"name": item.name, "start_time": str(item.start_time),
                 "end_time": str(item.end_time), "is_paid": item.is_paid}
                for item in day.breaks.all()
            ],
        }
        for day in sorted(days, key=lambda one: one.weekday)
    ]


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
            organization_id=actor.organization_id,
            # версии графика — его история, а не отдельные графики
            version_of__isnull=True,
        )
        # Графики организации видны всем с правом чтения; графики региона —
        # тем, кому виден этот регион (целиком или офисом в нём).
        scope = self.access.scope_for(actor, "schedules.read")
        if not scope.all_offices:
            queryset = queryset.filter(
                Q(region_id__isnull=True)
                | Q(region_id__in=scope.region_ids)
                | Q(region_id__in=Office.objects.filter(
                    id__in=scope.office_ids).values("region_id"))
            )
        if status:
            queryset = queryset.filter(status=status)
        if search:
            queryset = queryset.filter(name__icontains=search.strip())
        page = paginate(queryset.select_related("region"), limit=limit, cursor=cursor)
        self.mark_rights(actor, page.items)
        self.show_current_versions(actor, page.items)
        return page

    def show_current_versions(self, actor: Actor, schedules) -> None:
        """Показать у графика поля и дни версии, действующей СЕГОДНЯ.

        Назначения и права работают с исходной строкой графика, а читатель
        видит действующее расписание. Будущая версия («с понедельника»)
        видна в списке `versions` карточки.
        """
        from humotech.schedules.day_plan import version_on

        schedules = list(schedules)
        if not schedules:
            return
        day = self._today(actor)
        roots = {schedule.id for schedule in schedules}
        family: dict = {}
        for version in (
            WorkSchedule.objects.filter(Q(id__in=roots) | Q(version_of_id__in=roots))
            .prefetch_related("days__breaks")
        ):
            family.setdefault(version.root_id, []).append(version)
        for schedule in schedules:
            versions = family.get(schedule.id, [])
            current = version_on(versions, day) or schedule
            for field_name in VERSIONED_FIELDS:
                setattr(schedule, field_name, getattr(current, field_name))
            schedule.current_version = current
            schedule.version_list = sorted(
                versions, key=lambda one: (one.valid_from is not None, one.valid_from))

    def mark_rights(self, actor: Actor, schedules) -> None:
        """Проставить `can_manage` — может ли актор менять этот график.

        Интерфейсу нужно знать это заранее: кнопка «Изменить» у чужого
        графика звала бы к действию, которое сервер отклонит.
        """
        for schedule in schedules:
            try:
                self._require_editable(actor, schedule)
            except (PermissionDenied, NotFound):
                schedule.can_manage = False
            else:
                schedule.can_manage = True

    def get(self, actor: Actor, schedule_id: uuid.UUID) -> WorkSchedule:
        self.access.require(actor, "schedules.read")
        return self._detail(actor, schedule_id)

    def _detail(self, actor: Actor, schedule_id: uuid.UUID) -> WorkSchedule:
        """Карточка графика без проверки `schedules.read`: создание и правка
        возвращают результат, и требовать для этого право на чтение нельзя."""
        self.access.require_schedule(actor, schedule_id)
        # дни и перерывы — двумя дополнительными запросами на весь график,
        # а не по запросу на каждый день
        schedule = (
            WorkSchedule.objects.prefetch_related("days__breaks")
            .select_related("region").get(id=schedule_id)
        )
        # График чужого региона не открывается по угаданному id — по тому
        # же правилу, что и список.
        if schedule.region_id is not None:
            scope = self.access.scope_for(actor, "schedules.read")
            if not (scope.all_offices or schedule.region_id in scope.region_ids
                    or Office.objects.filter(id__in=scope.office_ids,
                                             region_id=schedule.region_id).exists()):
                raise NotFound("График работы не найден")
        self.mark_rights(actor, [schedule])
        self.show_current_versions(actor, [schedule])
        return schedule

    def days_of(self, schedule: WorkSchedule) -> list[ScheduleDay]:
        """Дни версии, действующей сегодня (у карточки) или самой строки."""
        current = getattr(schedule, "current_version", None) or schedule
        return sorted(current.days.all(), key=lambda day: day.weekday)

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
        region_id: uuid.UUID | None = None,
    ) -> WorkSchedule:
        self.access.require(actor, "schedules.manage")
        region_id = self._owner_region(actor, region_id)
        days = days or []
        self._validate_days(days)

        with self.atomic():
            schedule = WorkSchedule.objects.create(
                region_id=region_id,
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
        days: list[DaySpec] | None = None,
        effective_from: date | None = None,
        reason: str | None = None,
        **changes,
    ) -> WorkSchedule:
        """Правка графика. Переданный список дней заменяет расписание ЦЕЛИКОМ.

        Название и пояс — шапка графика, меняются сразу. Дни, перерывы,
        норма и допуски — расчёт: их правка у назначенного графика
        действует С ДАТЫ (`effective_from`, по умолчанию сегодня по поясу
        организации) и создаёт новую версию, а прошлые дни считаются по
        прежней (решение владельца Р-5, аудит C-10). Дата в прошлом здесь
        не принимается: правка задним числом — `change_history`.
        """
        self.access.require(actor, "schedules.manage")
        today = self._today(actor)
        start = effective_from or today
        if start < today:
            raise ValidationFailed(
                "Изменить график с прошедшей даты можно только отдельной "
                "операцией «Изменение задним числом» с причиной",
                details={"effective_from": str(start), "today": str(today),
                         "reason": "retroactive_requires_separate_operation"},
            )
        return self._change(actor, schedule_id, days=days, start=start,
                            reason=reason, retroactive=False, changes=changes)

    def change_history(
        self, actor: Actor, schedule_id: uuid.UUID, *,
        effective_from: date, reason: str,
        days: list[DaySpec] | None = None, **changes,
    ) -> WorkSchedule:
        """Правка графика задним числом — отдельная контролируемая операция.

        Меняет расчёт уже прошедших дней (опоздания, нормы, пропуски),
        поэтому требует отдельного права `schedules.change_history` и
        причины; версия помечается `is_retroactive`, всё — в журнал.
        """
        self.access.require(actor, "schedules.manage")
        self.access.require(actor, "schedules.change_history")
        reason = clean_text(reason, field="reason", required=True)
        if len(reason) < 5:
            raise ValidationFailed("Укажите причину изменения задним числом",
                                   details={"field": "reason"})
        return self._change(actor, schedule_id, days=days, start=effective_from,
                            reason=reason, retroactive=True, changes=changes)

    def _change(self, actor, schedule_id, *, days, start, reason, retroactive, changes):
        schedule = self.access.require_schedule(actor, schedule_id)
        self._require_editable(actor, schedule)

        before = snapshot(schedule, AUDITED_FIELDS)
        if changes.get("name") is not None:
            schedule.name = clean_text(changes["name"], field="name", required=True)
        if changes.get("timezone") is not None:
            schedule.timezone = validate_timezone(changes["timezone"])
        versioned = {
            name: changes[name] for name in VERSIONED_FIELDS
            if changes.get(name) is not None
        }
        if days is not None:
            self._validate_days(days)
        touches_days = days is not None or bool(versioned)

        with self.atomic():
            if touches_days:
                self._lock_holders(schedule_id, start)
            version = None
            before_days = after_days = None
            if touches_days:
                version, before_days, after_days = self._apply_version(
                    actor, schedule, start=start, days=days, versioned=versioned,
                    reason=reason, retroactive=retroactive,
                )
            schedule.save()
            after = snapshot(schedule, AUDITED_FIELDS)
            if version is not None:
                after = {
                    **after,
                    **{name: getattr(version, name) for name in VERSIONED_FIELDS},
                    "version_id": str(version.id),
                    "valid_from": str(version.valid_from) if version.valid_from else None,
                    "is_retroactive": retroactive,
                    "reason": reason,
                    "days": after_days,
                }
                before = {**before, "days": before_days}
            self.audit.record(
                actor,
                action="schedule.change_history" if retroactive else "schedule.update",
                entity_type="work_schedules", entity_id=schedule.id,
                before=before, after=after,
            )
        return self._detail(actor, schedule.id)

    def _today(self, actor: Actor) -> date:
        from humotech.core.timeframes import organization_zone, today as local_today

        return local_today(organization_zone(actor.organization_id))

    def _lock_holders(self, schedule_id, start: date) -> None:
        """Блокировки перед правкой расписания и проверка выездной работы.

        Порядок как при создании запроса выездной работы: сначала
        сотрудники, потом строка графика. Запрос выездной работы хранит
        снимок смены; правка, затрагивающая его день, ждёт его решения.
        Затронуты только дни начиная с `start`: прошлое версия не меняет.
        """
        from humotech.absences.services import lock_employee
        from humotech.attendance.field_work import (
            blocking_field_work, require_no_field_work,
        )

        # Блокировать нужно тех, кто может получить НОВЫЙ запрос выездной
        # работы одновременно с правкой: запрос создаётся только на сегодня
        # и только по действующему назначению. День запаса — офисы, у
        # которых «сегодня» по UTC ещё вчера.
        floor = min(start, date.today()) - timedelta(days=1)
        assigned = EmployeeScheduleAssignment.objects.filter(schedule_id=schedule_id)
        ids = list(assigned.filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=floor),
        ).values_list("employee_id", flat=True).distinct().order_by("employee_id"))
        for employee_id in ids:
            lock_employee(employee_id)
            Employee.objects.select_for_update().get(id=employee_id)
        WorkSchedule.objects.select_for_update().get(id=schedule_id)
        protected = blocking_field_work().filter(
            employee_id__in=assigned.values("employee_id"), date__gte=start)
        for protected_day in protected:
            active_assignment = EmployeeScheduleAssignment.objects.filter(
                employee_id=protected_day.employee_id, schedule_id=schedule_id,
                valid_from__lte=protected_day.date,
            ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=protected_day.date)).exists()
            if active_assignment:
                require_no_field_work(protected_day.employee_id,
                                      protected_day.date, protected_day.date)

    def _apply_version(self, actor, schedule, *, start, days, versioned, reason, retroactive):
        """Записать изменение расписания как версию с даты `start`.

        * График никому не назначали — истории, которую надо беречь, нет:
          правится сама строка, как раньше.
        * Версия, действующая на `start`, начинается ровно с `start` —
          правится она (вторая правка в тот же день не плодит версий).
        * Иначе версия делится: прежняя заканчивается накануне `start`,
          новая действует с `start` до конца прежней. Более поздние версии
          («с понедельника») не трогаются.
        """
        from humotech.schedules.day_plan import version_on

        family = list(
            WorkSchedule.objects.select_for_update()
            .filter(Q(id=schedule.id) | Q(version_of_id=schedule.id))
        )
        current = version_on(family, start)
        if current is None:
            raise Conflict("На эту дату у графика нет действующей версии",
                           details={"effective_from": str(start)})
        before_days = days_snapshot(current.days.prefetch_related("breaks"))
        used = EmployeeScheduleAssignment.objects.filter(schedule_id=schedule.id).exists()

        if not used or current.valid_from == start:
            target = schedule if current.id == schedule.id else current
            for name, value in versioned.items():
                setattr(target, name, value)
            target.save()
            if days is not None:
                self._replace_days(target, days)
        else:
            source_days = list(current.days.prefetch_related("breaks"))
            old_end = current.valid_to
            current.valid_to = start - timedelta(days=1)
            # Закрытие обязано дойти до базы ДО вставки новой версии:
            # EXCLUDE проверяется в момент выполнения оператора.
            current.save(update_fields=["valid_to", "updated_at"])
            if current.id == schedule.id:
                schedule.valid_to = current.valid_to
            target = WorkSchedule.objects.create(
                organization_id=schedule.organization_id,
                region_id=schedule.region_id,
                name=schedule.name,
                timezone=schedule.timezone,
                status=schedule.status,
                weekly_minutes=versioned.get("weekly_minutes", current.weekly_minutes),
                late_grace_minutes=versioned.get("late_grace_minutes",
                                                 current.late_grace_minutes),
                early_leave_grace_minutes=versioned.get(
                    "early_leave_grace_minutes", current.early_leave_grace_minutes),
                is_flexible=versioned.get("is_flexible", current.is_flexible),
                version_of_id=schedule.id,
                valid_from=start,
                valid_to=old_end,
                change_reason=reason,
                is_retroactive=retroactive,
                changed_by_user_id=actor.user_id,
            )
            if days is not None:
                self._replace_days(target, days)
            else:
                self._copy_days(target, source_days)
        after_days = days_snapshot(target.days.prefetch_related("breaks"))
        return target, before_days, after_days

    @staticmethod
    def _copy_days(target: WorkSchedule, source_days) -> None:
        for day in source_days:
            row = ScheduleDay.objects.create(
                schedule=target, weekday=day.weekday,
                is_working_day=day.is_working_day, start_time=day.start_time,
                end_time=day.end_time, crosses_midnight=day.crosses_midnight,
            )
            for item in day.breaks.all():
                ScheduleBreak.objects.create(
                    schedule_day=row, name=item.name, start_time=item.start_time,
                    end_time=item.end_time, is_paid=item.is_paid,
                )

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
        self._require_editable(actor, schedule)
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
        self._require_editable(actor, schedule)

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
        employee = self._require_employee(actor, employee_id, "schedules.manage")
        schedule = self.access.require_schedule(actor, schedule_id)
        self._require_assignable(schedule, employee)

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
            from humotech.attendance.field_work import require_no_field_work
            from humotech.attendance.models import FieldWorkRequest
            from humotech.absences.services import lock_employee
            lock_employee(employee_id)
            Employee.objects.select_for_update().get(id=employee_id)
            if FieldWorkRequest.objects.filter(employee_id=employee_id,
                                               date__gte=valid_from,
                                               status__in=("PENDING", "CONFIRMED")).exists():
                raise Conflict("Сначала отмените запрос выездной работы")
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
        # График и отдел проверяются ДО выборки состава: иначе чужой график
        # отвечал бы «в отделе нет сотрудников» вместо «не найден», а отказ
        # по области посреди цикла оставил бы половину назначений сделанной.
        self.access.require_schedule(actor, schedule_id)
        department = Department.objects.filter(
            id=department_id, organization_id=actor.organization_id
        ).first()
        if department is None:
            raise NotFound("Отдел не найден")
        if department.office_id is not None:
            self.access.require_office(actor, department.office_id,
                                       "schedules.manage")

        staff = EmployeeAssignment.objects.filter(
            department_id=department_id, organization_id=actor.organization_id
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=valid_from))
        # Общий отдел (без офиса) тянется через все регионы. Региональному
        # HR достаются только люди его области: назначить график человеку
        # чужого офиса по одному запросу он не может, и через отдел — тоже.
        visible = self.access.visible_office_ids(actor, "schedules.manage")
        if visible is not None:
            staff = staff.filter(office_id__in=visible)
        today_staff = list(staff.values_list("employee_id", flat=True).distinct())
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

    def _require_editable(self, actor: Actor, schedule: WorkSchedule) -> None:
        """Кто может менять, отключать и удалять график.

        График организации (без региона) — только держатель
        `schedules.manage` на всю организацию; график региона — держатель
        `schedules.manage` на весь этот регион. Область — в разрезе самого
        права: «вся организация» из соседнего назначения «только чтение»
        не считается (решение владельца 11.10.2026, аудит M-2).
        """
        if schedule.region_id is None:
            self.access.require_whole_organization(actor, "schedules.manage")
        else:
            self.access.require_whole_region(actor, schedule.region_id,
                                             "schedules.manage")

    def _owner_region(self, actor: Actor, region_id):
        """Регион нового графика.

        Указан — нужен весь этот регион. Не указан — график организации,
        если право есть на всю организацию; иначе — график единственного
        региона, выданного целиком (так региональный HR создаёт график, не
        выбирая регион). Нет ни того, ни другого — отказ.
        """
        if region_id is not None:
            self.access.require_whole_region(actor, region_id, "schedules.manage")
            return region_id
        scope = self.access.scope_for(actor, "schedules.manage")
        if scope.all_offices:
            return None
        if len(scope.region_ids) == 1:
            return next(iter(scope.region_ids))
        raise PermissionDenied(
            "Укажите регион графика: график организации создаёт только тот, "
            "у кого право на всю организацию",
            details={"field": "region_id", "reason": "scope_limited"},
        )

    def _require_assignable(self, schedule: WorkSchedule, employee) -> None:
        """График региона назначается только сотрудникам этого региона."""
        if schedule.region_id is None:
            return
        today = date.today()
        offices = EmployeeAssignment.objects.filter(
            employee_id=employee.id,
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today)).values("office_id")
        if not Office.objects.filter(id__in=offices, region_id=schedule.region_id).exists():
            raise ValidationFailed(
                "Это график другого региона: сотруднику его назначить нельзя",
                details={"schedule_id": str(schedule.id),
                         "reason": "schedule_of_other_region"},
            )

    def _require_employee(
        self, actor: Actor, employee_id: uuid.UUID, permission: str | None = None
    ) -> Employee:
        """Сотрудник своей организации И своей области видимости.

        Одной организации мало: региональный HR иначе назначал бы график
        и читал историю графиков людей чужого региона. `permission` для
        записи (назначение графика) берёт область в разрезе этого права,
        чтобы право из одного назначения не действовало на территории
        другого, выданного лишь на чтение.
        """
        return require_visible_employee(self.access, actor, employee_id, permission)

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
            if day.is_working_day and (
                day.crosses_midnight
                or (day.start_time is not None and day.end_time is not None
                    and day.end_time <= day.start_time)
            ):
                # Ночные смены через полночь система пока считать не умеет:
                # опоздание после полуночи терялось, день смены путался
                # (C-07). Пока их нельзя создать, а не считать неверно
                # (решение владельца Р-9).
                raise ValidationFailed(
                    "Ночные смены через полночь пока не поддерживаются: "
                    "конец смены должен быть позже начала в тот же день",
                    details={"weekday": day.weekday, "reason": "night_shift_unsupported"},
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
