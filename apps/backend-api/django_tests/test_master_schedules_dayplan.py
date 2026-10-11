"""Версии графика «с даты», запрет ночных смен, единый план дня (ТЗ 12.10).

Решения владельца: Р-5 (правка графика действует с выбранной даты, прошлое
считается по прежней версии, задним числом — отдельная операция), Р-9
(ночные смены пока не поддерживаются), D-03 (норма из графика и правил
перерывов, одна для всех), D-02 (календарь офиса, где человек был в тот
день), D-06 (рабочая суббота по переносу — рабочий день).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone

import pytest

from django_tests.conftest import make_office, make_qr_point
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.audit.models import AuditLog
from humotech.core.errors import NotFound, PermissionDenied, ValidationFailed
from humotech.core.timeframes import organization_zone, today as local_today
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.schedules.day_plan import plans_for
from humotech.schedules.models import CalendarException, WorkSchedule
from humotech.schedules.services import BreakSpec, DaySpec, WorkScheduleService

pytestmark = pytest.mark.django_db


@pytest.fixture()
def hr_actor(make_actor, organization):
    """HR с правом правки графика задним числом и чтением табеля."""
    from django_tests.conftest import HR_FULL_PERMISSIONS

    return make_actor(organization, permissions=tuple(dict.fromkeys(
        HR_FULL_PERMISSIONS + ("attendance.read", "schedules.change_history"))))

HR_NO_HISTORY = (
    "employees.read", "employees.manage", "schedules.read", "schedules.manage",
    "attendance.read", "calendar.manage",
)


def _week(start=time(9, 0), end=time(18, 0), *, lunch=True, paid_lunch=False, days=5):
    breaks = (BreakSpec(name="Обед", start_time=time(13, 0), end_time=time(14, 0),
                        is_paid=paid_lunch),) if lunch else ()
    return [
        DaySpec(weekday=w, is_working_day=w <= days, start_time=start if w <= days else None,
                end_time=end if w <= days else None, breaks=breaks if w <= days else ())
        for w in range(1, 8)
    ]


def _person(organization, office, number="S-1", hired=date(2026, 1, 1)):
    person = Employee.objects.create(
        organization=organization, employee_number=number, first_name="Пётр",
        last_name="Петров", hire_date=hired, employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=person, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=hired,
    )
    return person


def _came(organization, person, office, at, hours=8):
    point = make_qr_point(organization, office, code=f"P-{person.employee_number}-{at:%m%d%H}")
    entry = AttendanceEvent.objects.create(
        organization=organization, employee=person, office=office, qr_point=point,
        event_type="ENTRY", source="QR", verification_status="ACCEPTED", occurred_at=at,
    )
    left = at + timedelta(hours=hours)
    out = AttendanceEvent.objects.create(
        organization=organization, employee=person, office=office, qr_point=point,
        event_type="EXIT", source="QR", verification_status="ACCEPTED", occurred_at=left,
    )
    return AttendanceSession.objects.create(
        organization=organization, employee=person, office=office, entry_event=entry,
        exit_event=out, started_at=at, ended_at=left,
        duration_seconds=int(hours * 3600), status="CLOSED",
    )


def _late(actor, person, day):
    report = AttendanceHrService().daily(actor, person.id, first=day, last=day)
    return report["days"][0]["late_minutes"]


# --- версии графика ---------------------------------------------------------

class TestScheduleVersions:
    def test_editing_assigned_schedule_does_not_rewrite_past_lateness(
        self, organization, office, hr_actor
    ):
        """C-10: правка графика в октябре не меняет опоздания сентября."""
        service = WorkScheduleService()
        schedule = service.create(hr_actor, name="5/2", timezone="Asia/Dushanbe",
                                  weekly_minutes=2400, days=_week())
        person = _person(organization, office)
        service.assign_to_employee(hr_actor, employee_id=person.id,
                                   schedule_id=schedule.id, valid_from=date(2026, 9, 1))
        # Пн 07.09.2026, 09:30 по Душанбе (UTC+5) — 30 минут опоздания.
        past_day = date(2026, 9, 7)
        _came(organization, person, office, datetime(2026, 9, 7, 4, 30, tzinfo=dt_timezone.utc))
        assert _late(hr_actor, person, past_day) == 30

        service.update(hr_actor, schedule.id, days=_week(start=time(10, 0), end=time(19, 0)))

        assert _late(hr_actor, person, past_day) == 30

    def test_new_version_applies_from_its_date(self, organization, office, hr_actor):
        service = WorkScheduleService()
        schedule = service.create(hr_actor, name="5/2", timezone="Asia/Dushanbe",
                                  weekly_minutes=2400, days=_week())
        person = _person(organization, office)
        service.assign_to_employee(hr_actor, employee_id=person.id,
                                   schedule_id=schedule.id, valid_from=date(2026, 1, 5))
        today = local_today(organization_zone(organization.id))
        start = today + timedelta(days=3)
        service.update(hr_actor, schedule.id, effective_from=start,
                       days=_week(start=time(10, 0), end=time(19, 0)))

        plans = plans_for([person.id], today, start + timedelta(days=7))
        before = [p for (_, d), p in plans.items() if d < start and p.is_working]
        after = [p for (_, d), p in plans.items() if d >= start and p.is_working]
        assert after and all(p.start == time(10, 0) for p in after)
        assert all(p.start == time(9, 0) for p in before)

    def test_card_shows_today_version_and_lists_all_versions(
        self, organization, office, hr_actor
    ):
        service = WorkScheduleService()
        schedule = service.create(hr_actor, name="5/2", timezone="Asia/Dushanbe",
                                  weekly_minutes=2400, days=_week())
        person = _person(organization, office)
        service.assign_to_employee(hr_actor, employee_id=person.id,
                                   schedule_id=schedule.id, valid_from=date(2026, 1, 5))
        today = local_today(organization_zone(organization.id))
        service.update(hr_actor, schedule.id, effective_from=today + timedelta(days=10),
                       late_grace_minutes=15)

        card = service.get(hr_actor, schedule.id)
        assert card.late_grace_minutes == 0  # сегодня действует прежняя версия
        assert len(card.version_list) == 2
        assert service.list(hr_actor).size == 1  # версия — не отдельный график
        future = [v for v in card.version_list if v.version_of_id is not None][0]
        with pytest.raises(NotFound):
            service.get(hr_actor, future.id)

    def test_past_effective_date_needs_separate_operation(self, hr_actor):
        service = WorkScheduleService()
        schedule = service.create(hr_actor, name="5/2", timezone="Asia/Dushanbe",
                                  weekly_minutes=2400, days=_week())
        with pytest.raises(ValidationFailed) as error:
            service.update(hr_actor, schedule.id, effective_from=date(2026, 1, 1),
                           late_grace_minutes=5)
        assert error.value.details["reason"] == "retroactive_requires_separate_operation"

    def test_change_history_requires_its_own_permission(self, make_actor, organization):
        actor = make_actor(organization, permissions=HR_NO_HISTORY)
        service = WorkScheduleService()
        schedule = service.create(actor, name="5/2", timezone="Asia/Dushanbe",
                                  weekly_minutes=2400, days=_week())
        with pytest.raises(PermissionDenied):
            service.change_history(actor, schedule.id, effective_from=date(2026, 9, 1),
                                   reason="Приказ №14 от 01.09", late_grace_minutes=10)

    def test_change_history_changes_past_with_reason_and_audit(
        self, organization, office, hr_actor
    ):
        service = WorkScheduleService()
        schedule = service.create(hr_actor, name="5/2", timezone="Asia/Dushanbe",
                                  weekly_minutes=2400, days=_week())
        person = _person(organization, office)
        service.assign_to_employee(hr_actor, employee_id=person.id,
                                   schedule_id=schedule.id, valid_from=date(2026, 9, 1))
        past_day = date(2026, 9, 7)
        _came(organization, person, office, datetime(2026, 9, 7, 4, 30, tzinfo=dt_timezone.utc))

        service.change_history(hr_actor, schedule.id, effective_from=date(2026, 9, 7),
                               reason="Приказ №14: с 07.09 начало в 09:30",
                               days=_week(start=time(9, 30), end=time(18, 30)))

        assert _late(hr_actor, person, past_day) == 0
        version = WorkSchedule.objects.get(version_of_id=schedule.id)
        assert version.is_retroactive and version.valid_from == date(2026, 9, 7)
        log = AuditLog.objects.filter(action="schedule.change_history",
                                      entity_id=schedule.id).get()
        assert log.new_values["reason"].startswith("Приказ")
        assert log.old_values["days"][0]["start_time"] == "09:00:00"
        assert log.new_values["days"][0]["start_time"] == "09:30:00"

    def test_unassigned_schedule_is_edited_in_place(self, hr_actor):
        service = WorkScheduleService()
        schedule = service.create(hr_actor, name="Черновик", timezone="Asia/Dushanbe",
                                  weekly_minutes=2400, days=_week())
        service.update(hr_actor, schedule.id, days=_week(start=time(8, 0), end=time(17, 0)))
        assert not WorkSchedule.objects.filter(version_of_id=schedule.id).exists()
        assert service.days_of(service.get(hr_actor, schedule.id))[0].start_time == time(8, 0)


# --- ночные смены -------------------------------------------------------------

class TestNightShifts:
    @pytest.mark.parametrize("day", [
        DaySpec(weekday=1, is_working_day=True, start_time=time(22, 0),
                end_time=time(6, 0), crosses_midnight=True),
        DaySpec(weekday=1, is_working_day=True, start_time=time(22, 0), end_time=time(6, 0)),
    ])
    def test_night_shift_is_rejected(self, hr_actor, day):
        with pytest.raises(ValidationFailed) as error:
            WorkScheduleService().create(hr_actor, name="Ночь", timezone="Asia/Dushanbe",
                                         weekly_minutes=2400, days=[day])
        assert error.value.details["reason"] == "night_shift_unsupported"


# --- единый план дня ----------------------------------------------------------

class TestDayPlan:
    def _setup(self, organization, office, hr_actor, days=None, valid_from=date(2026, 1, 5)):
        service = WorkScheduleService()
        schedule = service.create(hr_actor, name="5/2", timezone="Asia/Dushanbe",
                                  weekly_minutes=2400, days=days or _week())
        person = _person(organization, office)
        service.assign_to_employee(hr_actor, employee_id=person.id,
                                   schedule_id=schedule.id, valid_from=valid_from)
        return person

    def _plan(self, person, day):
        return plans_for([person.id], day, day)[(person.id, day)]

    def test_norm_is_shift_minus_unpaid_breaks(self, organization, office, hr_actor):
        """D-03: 09–18 с неоплачиваемым обедом 13–14 — 8 часов, не 9."""
        person = self._setup(organization, office, hr_actor)
        assert self._plan(person, date(2026, 9, 7)).norm_seconds == 8 * 3600

    def test_paid_break_is_not_deducted(self, organization, office, hr_actor):
        person = self._setup(organization, office, hr_actor, days=_week(paid_lunch=True))
        assert self._plan(person, date(2026, 9, 7)).norm_seconds == 9 * 3600

    def test_working_weekend_is_working_without_invented_norm(
        self, organization, office, hr_actor
    ):
        """D-06: суббота по переносу — рабочий день; норму график не задаёт (П-11)."""
        person = self._setup(organization, office, hr_actor)
        CalendarException.objects.create(
            organization=organization, date=date(2026, 9, 12), name="Перенос",
            exception_type="WORKING_WEEKEND", is_working_day=True)
        plan = self._plan(person, date(2026, 9, 12))
        assert plan.is_working is True
        assert plan.norm_seconds is None and plan.start is None

    def test_calendar_of_the_office_of_that_day(self, organization, region, office, hr_actor):
        """D-02: праздник прежнего офиса действует только в дни, когда человек был там."""
        person = self._setup(organization, office, hr_actor)
        other = make_office(organization, region, "NEW")
        EmployeeAssignment.objects.filter(employee=person).update(valid_to=date(2026, 9, 15))
        EmployeeAssignment.objects.create(
            organization=organization, employee=person, office=other,
            employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
            valid_from=date(2026, 9, 16))
        for day in (date(2026, 9, 10), date(2026, 9, 17)):
            CalendarException.objects.create(
                organization=organization, office=office, date=day,
                name="Праздник офиса", exception_type="HOLIDAY", is_working_day=False)
        plans = plans_for([person.id], date(2026, 9, 10), date(2026, 9, 17))
        assert plans[(person.id, date(2026, 9, 10))].is_working is False
        assert plans[(person.id, date(2026, 9, 17))].is_working is True
        assert plans[(person.id, date(2026, 9, 17))].office_id == other.id

    def test_day_is_settled_after_shift_end(self, organization, office, hr_actor):
        """D-01: до конца смены день ещё не прошёл."""
        person = self._setup(organization, office, hr_actor)
        plan = self._plan(person, date(2026, 9, 7))
        # 18:00 по Душанбе = 13:00 UTC
        assert not plan.is_settled(datetime(2026, 9, 7, 12, 59, tzinfo=dt_timezone.utc))
        assert plan.is_settled(datetime(2026, 9, 7, 13, 0, tzinfo=dt_timezone.utc))
