"""Рабочие графики: создание, интервалы, назначение сотруднику, чужая организация.

Перенос `tests/integration/test_hr_schedules.py` на Django ORM.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from humotech.audit.models import AuditLog
from humotech.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from humotech.schedules.models import EmployeeScheduleAssignment
from humotech.schedules.services import BreakSpec, DaySpec, WorkScheduleService

pytestmark = pytest.mark.django_db


def _workweek() -> list[DaySpec]:
    return [
        DaySpec(
            weekday=weekday, is_working_day=True,
            start_time=time(9, 0), end_time=time(18, 0),
            breaks=(BreakSpec(name="Обед", start_time=time(13, 0),
                              end_time=time(14, 0)),),
        )
        for weekday in range(1, 6)
    ] + [
        DaySpec(weekday=6, is_working_day=False),
        DaySpec(weekday=7, is_working_day=False),
    ]


# --- создание и редактирование ---------------------------------------------

def test_hr_creates_schedule_with_days_and_breaks(organization, hr_actor):
    service = WorkScheduleService()
    schedule = service.create(
        hr_actor, name="Пятидневка 09:00-18:00", timezone="Asia/Dushanbe",
        weekly_minutes=2400, late_grace_minutes=10, days=_workweek(),
    )
    assert schedule.status == "ACTIVE"
    assert schedule.organization_id == organization.id

    days = service.days_of(schedule)
    assert [day.weekday for day in days] == [1, 2, 3, 4, 5, 6, 7]
    assert list(days[0].breaks.all())[0].name == "Обед"
    assert days[5].is_working_day is False


def test_schedule_update_replaces_days_completely(hr_actor):
    service = WorkScheduleService()
    schedule = service.create(hr_actor, name="График", timezone="Asia/Dushanbe",
                              weekly_minutes=2400, days=_workweek())

    updated = service.update(
        hr_actor, schedule.id, name="График 2",
        days=[DaySpec(weekday=1, is_working_day=True,
                      start_time=time(10, 0), end_time=time(19, 0))],
    )
    assert updated.name == "График 2"
    days = service.days_of(updated)
    assert [day.weekday for day in days] == [1]
    assert days[0].start_time == time(10, 0)


def test_schedule_list_search_and_status_filter(organization, hr_actor):
    service = WorkScheduleService()
    for name in ("Дневная смена", "Ночная смена", "Гибкий график"):
        service.create(hr_actor, name=name, timezone="Asia/Dushanbe",
                       weekly_minutes=2400)

    assert service.list(hr_actor, search="смена").size == 2
    inactive = service.list(hr_actor, search="Гибкий").items[0]
    service.deactivate(hr_actor, inactive.id)
    assert service.list(hr_actor, status="INACTIVE").size == 1
    assert service.list(hr_actor, status="ACTIVE").size == 2


# --- некорректные интервалы -------------------------------------------------

def test_end_before_start_is_rejected(hr_actor):
    with pytest.raises(ValidationFailed, match="раньше его начала"):
        WorkScheduleService().create(
            hr_actor, name="Плохой", timezone="Asia/Dushanbe",
            weekly_minutes=2400,
            days=[DaySpec(weekday=1, is_working_day=True,
                          start_time=time(18, 0), end_time=time(9, 0))])


def test_night_shift_is_allowed_when_marked_explicitly(hr_actor):
    """22:00–06:00 — не опечатка, если помечено `crosses_midnight`.

    Именно поэтому проверку нельзя было отдать CHECK-ограничению: без флага
    ночная смена и перепутанные местами часы выглядят одинаково.
    """
    service = WorkScheduleService()
    schedule = service.create(
        hr_actor, name="Ночная", timezone="Asia/Dushanbe", weekly_minutes=2400,
        days=[DaySpec(weekday=1, is_working_day=True, start_time=time(22, 0),
                      end_time=time(6, 0), crosses_midnight=True)])
    assert service.days_of(schedule)[0].crosses_midnight is True


def test_night_shift_flag_without_night_shift_is_rejected(hr_actor):
    with pytest.raises(ValidationFailed, match="Ночная смена"):
        WorkScheduleService().create(
            hr_actor, name="Странный", timezone="Asia/Dushanbe",
            weekly_minutes=2400,
            days=[DaySpec(weekday=1, is_working_day=True, start_time=time(9, 0),
                          end_time=time(18, 0), crosses_midnight=True)])


def test_zero_length_day_is_rejected(hr_actor):
    with pytest.raises(ValidationFailed, match="нулевой длины"):
        WorkScheduleService().create(
            hr_actor, name="Нулевой", timezone="Asia/Dushanbe",
            weekly_minutes=2400,
            days=[DaySpec(weekday=1, is_working_day=True, start_time=time(9, 0),
                          end_time=time(9, 0))])


def test_break_outside_working_hours_is_rejected(hr_actor):
    with pytest.raises(ValidationFailed, match="за границы рабочего дня"):
        WorkScheduleService().create(
            hr_actor, name="Обед не там", timezone="Asia/Dushanbe",
            weekly_minutes=2400,
            days=[DaySpec(
                weekday=1, is_working_day=True, start_time=time(9, 0),
                end_time=time(18, 0),
                breaks=(BreakSpec(name="Обед", start_time=time(19, 0),
                                  end_time=time(20, 0)),))])


def test_duplicate_weekday_is_rejected(hr_actor):
    with pytest.raises(ValidationFailed, match="дважды"):
        WorkScheduleService().create(
            hr_actor, name="Дубль", timezone="Asia/Dushanbe",
            weekly_minutes=2400,
            days=[DaySpec(weekday=1, is_working_day=False),
                  DaySpec(weekday=1, is_working_day=False)])


def test_non_working_day_with_time_is_rejected(hr_actor):
    with pytest.raises(ValidationFailed, match="нерабочего дня"):
        WorkScheduleService().create(
            hr_actor, name="Выходной с часами", timezone="Asia/Dushanbe",
            weekly_minutes=2400,
            days=[DaySpec(weekday=6, is_working_day=False,
                          start_time=time(9, 0), end_time=time(18, 0))])


def test_schedule_timezone_must_exist(hr_actor):
    with pytest.raises(ValidationFailed, match="часовой пояс"):
        WorkScheduleService().create(
            hr_actor, name="Плохой пояс", timezone="Mars/Olympus",
            weekly_minutes=2400)


# --- 3. Назначение графика сотруднику ---------------------------------------

def test_hr_assigns_schedule_to_employee(employee, work_schedule, hr_actor):
    service = WorkScheduleService()
    assignment = service.assign_to_employee(
        hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
        valid_from=date(2025, 1, 1),
    )
    assert assignment.schedule_id == work_schedule.id
    assert assignment.valid_to is None
    assert assignment.assigned_by_user_id == hr_actor.user_id

    entry = AuditLog.objects.get(action="employee.schedule.assign")
    assert entry.new_values["schedule_id"] == str(work_schedule.id)


def test_schedule_change_closes_previous_period(
    organization, employee, work_schedule, hr_actor
):
    """Смена графика закрывает прошлый период днём раньше начала нового.

    Границы периода в EXCLUDE-ограничении включительные: закрытие той же датой,
    с которой начинается новый график, считалось бы пересечением.
    """
    from django_tests.conftest import make_work_schedule

    service = WorkScheduleService()
    second = make_work_schedule(organization, name="Сменный")

    service.assign_to_employee(hr_actor, employee_id=employee.id,
                               schedule_id=work_schedule.id,
                               valid_from=date(2025, 1, 1))
    service.assign_to_employee(hr_actor, employee_id=employee.id,
                               schedule_id=second.id,
                               valid_from=date(2025, 6, 1))

    periods = list(
        EmployeeScheduleAssignment.objects.filter(employee=employee)
        .order_by("valid_from")
    )
    assert len(periods) == 2, "старый период сохранён, а не переписан"
    assert periods[0].valid_to == date(2025, 5, 31)
    assert periods[1].valid_from == date(2025, 6, 1)
    assert periods[1].valid_to is None

    history = service.history(hr_actor, employee.id)
    assert [row.schedule_id for row in history] == [second.id, work_schedule.id]


def test_schedule_change_backwards_in_time_is_rejected(
    organization, employee, work_schedule, hr_actor
):
    from django_tests.conftest import make_work_schedule

    service = WorkScheduleService()
    second = make_work_schedule(organization, name="Второй")
    service.assign_to_employee(hr_actor, employee_id=employee.id,
                               schedule_id=work_schedule.id,
                               valid_from=date(2025, 6, 1))
    with pytest.raises(ValidationFailed, match="позже начала действующего"):
        service.assign_to_employee(hr_actor, employee_id=employee.id,
                                   schedule_id=second.id,
                                   valid_from=date(2025, 3, 1))


# --- 10. График другой организации нельзя назначить -------------------------

def test_foreign_schedule_cannot_be_assigned(employee, foreign_schedule, hr_actor):
    with pytest.raises(NotFound, match="График работы не найден"):
        WorkScheduleService().assign_to_employee(
            hr_actor, employee_id=employee.id, schedule_id=foreign_schedule.id,
            valid_from=date(2025, 1, 1),
        )


def test_foreign_schedule_is_invisible_in_list_and_detail(
    hr_actor, foreign_schedule, work_schedule
):
    service = WorkScheduleService()
    ids = {item.id for item in service.list(hr_actor).items}
    assert work_schedule.id in ids
    assert foreign_schedule.id not in ids
    with pytest.raises(NotFound):
        service.get(hr_actor, foreign_schedule.id)


def test_foreign_employee_cannot_get_our_schedule(
    employee, work_schedule, foreign_actor
):
    with pytest.raises(NotFound, match="Сотрудник не найден"):
        WorkScheduleService().assign_to_employee(
            foreign_actor, employee_id=employee.id, schedule_id=work_schedule.id,
            valid_from=date(2025, 1, 1),
        )


# --- деактивированный график ------------------------------------------------

def test_inactive_schedule_cannot_be_assigned(employee, work_schedule, hr_actor):
    service = WorkScheduleService()
    service.deactivate(hr_actor, work_schedule.id)
    with pytest.raises(Conflict, match="График не активен"):
        service.assign_to_employee(hr_actor, employee_id=employee.id,
                                   schedule_id=work_schedule.id,
                                   valid_from=date(2025, 1, 1))


def test_deactivating_schedule_keeps_existing_assignments(
    employee, work_schedule, hr_actor
):
    """Выключенный график перестаёт назначаться, но прошлые периоды остаются:
    по ним считается уже отработанное время."""
    service = WorkScheduleService()
    service.assign_to_employee(hr_actor, employee_id=employee.id,
                               schedule_id=work_schedule.id,
                               valid_from=date(2025, 1, 1))
    service.deactivate(hr_actor, work_schedule.id)

    assert len(service.history(hr_actor, employee.id)) == 1
    assert service.reactivate(hr_actor, work_schedule.id).status == "ACTIVE"


# --- права ------------------------------------------------------------------

def test_readonly_user_cannot_touch_schedules(
    employee, work_schedule, readonly_actor
):
    service = WorkScheduleService()
    assert service.get(readonly_actor, work_schedule.id).id == work_schedule.id

    with pytest.raises(PermissionDenied, match="schedules.manage"):
        service.create(readonly_actor, name="Новый", timezone="Asia/Dushanbe",
                       weekly_minutes=2400)
    with pytest.raises(PermissionDenied, match="schedules.manage"):
        service.assign_to_employee(readonly_actor, employee_id=employee.id,
                                   schedule_id=work_schedule.id,
                                   valid_from=date(2025, 1, 1))
    with pytest.raises(PermissionDenied, match="schedules.manage"):
        service.deactivate(readonly_actor, work_schedule.id)


def test_user_without_schedule_permissions_cannot_read(work_schedule, nobody_actor):
    with pytest.raises(PermissionDenied, match="schedules.read"):
        WorkScheduleService().list(nobody_actor)


def test_manage_without_read_still_returns_the_schedule(organization, make_actor):
    """Право на изменение не должно требовать права на чтение.

    Создание возвращает карточку результата. Если её сборка требует
    `schedules.read`, роль без этого права сохранила бы график и следом
    получила отказ — исключение при уже применённых изменениях.
    """
    actor = make_actor(organization, permissions=("schedules.manage",))
    service = WorkScheduleService()
    schedule = service.create(actor, name="Только запись",
                              timezone="Asia/Dushanbe", weekly_minutes=2400)
    assert schedule.status == "ACTIVE"
    with pytest.raises(PermissionDenied, match="schedules.read"):
        service.get(actor, schedule.id)
