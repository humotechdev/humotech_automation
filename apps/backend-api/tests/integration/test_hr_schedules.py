"""Рабочие графики: создание, интервалы, назначение сотруднику, чужая организация."""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from src.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from src.modules.audit.models import AuditLog
from src.modules.schedules.models import EmployeeScheduleAssignment
from src.modules.schedules.schemas import (
    ScheduleBreakSpec,
    ScheduleDaySpec,
    WorkScheduleCreateRequest,
    WorkScheduleUpdateRequest,
)
from src.modules.schedules.service import WorkScheduleService

pytestmark = pytest.mark.usefixtures("engine")


def _workweek() -> list[ScheduleDaySpec]:
    return [
        ScheduleDaySpec(
            weekday=weekday, is_working_day=True,
            start_time=time(9, 0), end_time=time(18, 0),
            breaks=[ScheduleBreakSpec(name="Обед", start_time=time(13, 0),
                                      end_time=time(14, 0))],
        )
        for weekday in range(1, 6)
    ] + [
        ScheduleDaySpec(weekday=6, is_working_day=False),
        ScheduleDaySpec(weekday=7, is_working_day=False),
    ]


# --- создание и редактирование ---------------------------------------------

def test_hr_creates_schedule_with_days_and_breaks(db, organization, hr_actor):
    schedule = WorkScheduleService(db).create(
        hr_actor,
        WorkScheduleCreateRequest(
            name="Пятидневка 09:00-18:00", timezone="Asia/Dushanbe",
            weekly_minutes=2400, late_grace_minutes=10, days=_workweek(),
        ),
    )
    assert schedule.status == "ACTIVE"
    assert schedule.organization_id == organization.id
    assert [day.weekday for day in schedule.days] == [1, 2, 3, 4, 5, 6, 7]
    assert schedule.days[0].breaks[0].name == "Обед"
    assert schedule.days[5].is_working_day is False


def test_schedule_update_replaces_days_completely(db, hr_actor):
    service = WorkScheduleService(db)
    schedule = service.create(hr_actor, WorkScheduleCreateRequest(
        name="График", timezone="Asia/Dushanbe", weekly_minutes=2400,
        days=_workweek()))

    updated = service.update(
        hr_actor, schedule.id,
        WorkScheduleUpdateRequest(
            name="График 2",
            days=[ScheduleDaySpec(weekday=1, is_working_day=True,
                                  start_time=time(10, 0), end_time=time(19, 0))],
        ),
    )
    assert updated.name == "График 2"
    assert [day.weekday for day in updated.days] == [1]
    assert updated.days[0].start_time == time(10, 0)


def test_schedule_list_search_and_status_filter(db, organization, hr_actor):
    service = WorkScheduleService(db)
    for name in ("Дневная смена", "Ночная смена", "Гибкий график"):
        service.create(hr_actor, WorkScheduleCreateRequest(
            name=name, timezone="Asia/Dushanbe", weekly_minutes=2400))

    assert service.list(hr_actor, search="смена").size == 2
    inactive = service.list(hr_actor, search="Гибкий").items[0]
    service.deactivate(hr_actor, inactive.id)
    assert service.list(hr_actor, status="INACTIVE").size == 1
    assert service.list(hr_actor, status="ACTIVE").size == 2


# --- некорректные интервалы -------------------------------------------------

def test_end_before_start_is_rejected(db, hr_actor):
    with pytest.raises(ValidationFailed, match="раньше его начала"):
        WorkScheduleService(db).create(hr_actor, WorkScheduleCreateRequest(
            name="Плохой", timezone="Asia/Dushanbe", weekly_minutes=2400,
            days=[ScheduleDaySpec(weekday=1, is_working_day=True,
                                  start_time=time(18, 0), end_time=time(9, 0))]))


def test_night_shift_is_allowed_when_marked_explicitly(db, hr_actor):
    """22:00–06:00 — не опечатка, если помечено `crosses_midnight`.

    Именно поэтому проверку нельзя было отдать CHECK-ограничению: без флага
    ночная смена и перепутанные местами часы выглядят одинаково.
    """
    schedule = WorkScheduleService(db).create(hr_actor, WorkScheduleCreateRequest(
        name="Ночная", timezone="Asia/Dushanbe", weekly_minutes=2400,
        days=[ScheduleDaySpec(weekday=1, is_working_day=True,
                              start_time=time(22, 0), end_time=time(6, 0),
                              crosses_midnight=True)]))
    assert schedule.days[0].crosses_midnight is True


def test_night_shift_flag_without_night_shift_is_rejected(db, hr_actor):
    with pytest.raises(ValidationFailed, match="Ночная смена"):
        WorkScheduleService(db).create(hr_actor, WorkScheduleCreateRequest(
            name="Странный", timezone="Asia/Dushanbe", weekly_minutes=2400,
            days=[ScheduleDaySpec(weekday=1, is_working_day=True,
                                  start_time=time(9, 0), end_time=time(18, 0),
                                  crosses_midnight=True)]))


def test_zero_length_day_is_rejected(db, hr_actor):
    with pytest.raises(ValidationFailed, match="нулевой длины"):
        WorkScheduleService(db).create(hr_actor, WorkScheduleCreateRequest(
            name="Нулевой", timezone="Asia/Dushanbe", weekly_minutes=2400,
            days=[ScheduleDaySpec(weekday=1, is_working_day=True,
                                  start_time=time(9, 0), end_time=time(9, 0))]))


def test_break_outside_working_hours_is_rejected(db, hr_actor):
    with pytest.raises(ValidationFailed, match="за границы рабочего дня"):
        WorkScheduleService(db).create(hr_actor, WorkScheduleCreateRequest(
            name="Обед не там", timezone="Asia/Dushanbe", weekly_minutes=2400,
            days=[ScheduleDaySpec(
                weekday=1, is_working_day=True, start_time=time(9, 0),
                end_time=time(18, 0),
                breaks=[ScheduleBreakSpec(name="Обед", start_time=time(19, 0),
                                          end_time=time(20, 0))])]))


def test_duplicate_weekday_is_rejected(db, hr_actor):
    with pytest.raises(ValidationFailed, match="дважды"):
        WorkScheduleService(db).create(hr_actor, WorkScheduleCreateRequest(
            name="Дубль", timezone="Asia/Dushanbe", weekly_minutes=2400,
            days=[ScheduleDaySpec(weekday=1, is_working_day=False),
                  ScheduleDaySpec(weekday=1, is_working_day=False)]))


def test_non_working_day_with_time_is_rejected(db, hr_actor):
    with pytest.raises(ValidationFailed, match="нерабочего дня"):
        WorkScheduleService(db).create(hr_actor, WorkScheduleCreateRequest(
            name="Выходной с часами", timezone="Asia/Dushanbe",
            weekly_minutes=2400,
            days=[ScheduleDaySpec(weekday=6, is_working_day=False,
                                  start_time=time(9, 0), end_time=time(18, 0))]))


def test_schedule_timezone_must_exist(db, hr_actor):
    with pytest.raises(ValidationFailed, match="часовой пояс"):
        WorkScheduleService(db).create(hr_actor, WorkScheduleCreateRequest(
            name="Плохой пояс", timezone="Mars/Olympus", weekly_minutes=2400))


# --- 3. Назначение графика сотруднику ---------------------------------------

def test_hr_assigns_schedule_to_employee(
    db, organization, employee, work_schedule, hr_actor
):
    service = WorkScheduleService(db)
    assignment = service.assign_to_employee(
        hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
        valid_from=date(2025, 1, 1),
    )
    assert assignment.schedule_id == work_schedule.id
    assert assignment.valid_to is None
    assert assignment.assigned_by_user_id == hr_actor.user_id

    entry = db.scalars(
        select(AuditLog).where(AuditLog.action == "employee.schedule.assign")
    ).one()
    assert entry.new_values["schedule_id"] == str(work_schedule.id)


def test_schedule_change_closes_previous_period(
    db, organization, employee, work_schedule, hr_actor
):
    """Смена графика закрывает прошлый период днём раньше начала нового.

    Границы периода в EXCLUDE-ограничении включительные: закрытие той же датой,
    с которой начинается новый график, считалось бы пересечением.
    """
    from tests.conftest import make_work_schedule

    service = WorkScheduleService(db)
    second = make_work_schedule(db, organization, name="Сменный")

    service.assign_to_employee(hr_actor, employee_id=employee.id,
                               schedule_id=work_schedule.id,
                               valid_from=date(2025, 1, 1))
    service.assign_to_employee(hr_actor, employee_id=employee.id,
                               schedule_id=second.id,
                               valid_from=date(2025, 6, 1))

    periods = db.scalars(
        select(EmployeeScheduleAssignment)
        .where(EmployeeScheduleAssignment.employee_id == employee.id)
        .order_by(EmployeeScheduleAssignment.valid_from)
    ).all()
    assert len(periods) == 2, "старый период сохранён, а не переписан"
    assert periods[0].valid_to == date(2025, 5, 31)
    assert periods[1].valid_from == date(2025, 6, 1)
    assert periods[1].valid_to is None

    history = service.history(hr_actor, employee.id)
    assert [row.schedule_id for row in history] == [second.id, work_schedule.id]


def test_schedule_change_backwards_in_time_is_rejected(
    db, organization, employee, work_schedule, hr_actor
):
    from tests.conftest import make_work_schedule

    service = WorkScheduleService(db)
    second = make_work_schedule(db, organization, name="Второй")
    service.assign_to_employee(hr_actor, employee_id=employee.id,
                               schedule_id=work_schedule.id,
                               valid_from=date(2025, 6, 1))
    with pytest.raises(ValidationFailed, match="позже начала действующего"):
        service.assign_to_employee(hr_actor, employee_id=employee.id,
                                   schedule_id=second.id,
                                   valid_from=date(2025, 3, 1))


# --- 10. График другой организации нельзя назначить -------------------------

def test_foreign_schedule_cannot_be_assigned(
    db, employee, foreign_schedule, hr_actor
):
    with pytest.raises(NotFound, match="График работы не найден"):
        WorkScheduleService(db).assign_to_employee(
            hr_actor, employee_id=employee.id, schedule_id=foreign_schedule.id,
            valid_from=date(2025, 1, 1),
        )


def test_foreign_schedule_is_invisible_in_list_and_detail(
    db, hr_actor, foreign_schedule, work_schedule
):
    service = WorkScheduleService(db)
    ids = {item.id for item in service.list(hr_actor).items}
    assert work_schedule.id in ids
    assert foreign_schedule.id not in ids
    with pytest.raises(NotFound):
        service.get(hr_actor, foreign_schedule.id)


def test_foreign_employee_cannot_get_our_schedule(
    db, employee, work_schedule, foreign_actor
):
    with pytest.raises(NotFound, match="Сотрудник не найден"):
        WorkScheduleService(db).assign_to_employee(
            foreign_actor, employee_id=employee.id, schedule_id=work_schedule.id,
            valid_from=date(2025, 1, 1),
        )


# --- деактивированный график ------------------------------------------------

def test_inactive_schedule_cannot_be_assigned(
    db, organization, employee, work_schedule, hr_actor
):
    service = WorkScheduleService(db)
    service.deactivate(hr_actor, work_schedule.id)
    with pytest.raises(Conflict, match="График не активен"):
        service.assign_to_employee(hr_actor, employee_id=employee.id,
                                   schedule_id=work_schedule.id,
                                   valid_from=date(2025, 1, 1))


def test_deactivating_schedule_keeps_existing_assignments(
    db, employee, work_schedule, hr_actor
):
    """Выключенный график перестаёт назначаться, но прошлые периоды остаются:
    по ним считается уже отработанное время."""
    service = WorkScheduleService(db)
    service.assign_to_employee(hr_actor, employee_id=employee.id,
                               schedule_id=work_schedule.id,
                               valid_from=date(2025, 1, 1))
    service.deactivate(hr_actor, work_schedule.id)

    assert len(service.history(hr_actor, employee.id)) == 1
    assert service.reactivate(hr_actor, work_schedule.id).status == "ACTIVE"


# --- права ------------------------------------------------------------------

def test_readonly_user_cannot_touch_schedules(
    db, employee, work_schedule, readonly_actor
):
    service = WorkScheduleService(db)
    assert service.get(readonly_actor, work_schedule.id).id == work_schedule.id

    with pytest.raises(PermissionDenied, match="schedules.manage"):
        service.create(readonly_actor, WorkScheduleCreateRequest(
            name="Новый", timezone="Asia/Dushanbe", weekly_minutes=2400))
    with pytest.raises(PermissionDenied, match="schedules.manage"):
        service.assign_to_employee(readonly_actor, employee_id=employee.id,
                                   schedule_id=work_schedule.id,
                                   valid_from=date(2025, 1, 1))
    with pytest.raises(PermissionDenied, match="schedules.manage"):
        service.deactivate(readonly_actor, work_schedule.id)


def test_user_without_schedule_permissions_cannot_read(db, work_schedule, nobody_actor):
    with pytest.raises(PermissionDenied, match="schedules.read"):
        WorkScheduleService(db).list(nobody_actor)
