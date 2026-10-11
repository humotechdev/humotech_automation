"""Этап 4, праздники офисов, перенос и перевод в середине месяца: C-13, D-06, D-02, B-04.

test_known_*/test_new_* ОЖИДАЕМО ПАДАЮТ (доказательство), test_baseline_* проходят.
Сентябрь 2026: 1.09 — вторник, 22 рабочих дня (11 до 15.09 включительно, 11 после).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction

from django_tests.conftest import link_telegram
from humotech.absences.models import AbsenceRequest, AbsenceType, EmployeeAbsence
from humotech.analytics.metrics import AnalyticsService
from humotech.analytics.overview import OverviewService
from humotech.attendance import statistics
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)
from humotech.telegram.identity import EmployeeContext, resolve_by_telegram_user_id

pytestmark = pytest.mark.django_db

TZ = ZoneInfo("Asia/Dushanbe")
ALL = ("analytics.read", "employees.read", "attendance.read", "reports.export",
       "absences.read", "offices.read", "schedules.read")
MON, TUE = date(2026, 9, 7), date(2026, 9, 8)


# --- помощники (одинаковые во всех test_s4_*) -------------------------------

def at(day, hh, mm=0, tz=TZ):
    return datetime.combine(day, time(hh, mm), tzinfo=tz).astimezone(dt_timezone.utc)


def mark(employee, office, moment, kind, verification="ACCEPTED"):
    return AttendanceEvent.objects.create(
        organization_id=employee.organization_id, employee=employee, office=office,
        event_type=kind, source="MANUAL", verification_status=verification,
        occurred_at=moment, received_at=moment,
    )


def visit(employee, office, start, end=None, verification="ACCEPTED"):
    return AttendanceSession.objects.create(
        organization_id=employee.organization_id, employee=employee, office=office,
        entry_event=mark(employee, office, start, "ENTRY", verification),
        exit_event=mark(employee, office, end, "EXIT") if end else None,
        started_at=start, ended_at=end,
        duration_seconds=int((end - start).total_seconds()) if end else None,
        status="CLOSED" if end else "OPEN",
    )


def person(organization, office, *, valid_from=date(2024, 1, 1), valid_to=None,
           status="ACTIVE", termination=None):
    emp = Employee.objects.create(
        organization=organization, employee_number=f"S4-{uuid.uuid4().hex[:8]}",
        first_name="Эталон", last_name=f"Сотрудник-{uuid.uuid4().hex[:4]}",
        hire_date=date(2024, 1, 1), employment_status=status,
        termination_date=termination,
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=emp, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=valid_from, valid_to=valid_to,
    )
    return emp


def schedule(organization, employee, *, start=time(9), end=time(18), weekly=2700,
             grace=0, weekdays=(1, 2, 3, 4, 5), crosses=False,
             valid_from=date(2024, 1, 1)):
    sched = WorkSchedule.objects.create(
        organization=organization, name=f"Эталон {uuid.uuid4().hex[:6]}",
        timezone="Asia/Dushanbe", weekly_minutes=weekly,
        late_grace_minutes=grace, status="ACTIVE",
    )
    for weekday in range(1, 8):
        working = weekday in weekdays
        ScheduleDay.objects.create(
            schedule=sched, weekday=weekday, is_working_day=working,
            start_time=start if working else None, end_time=end if working else None,
            crosses_midnight=bool(crosses and working),
        )
    EmployeeScheduleAssignment.objects.create(
        organization=organization, employee=employee, schedule=sched,
        valid_from=valid_from,
    )
    return sched


def context(employee, tg_id):
    link_telegram(employee, telegram_user_id=tg_id)
    ctx = resolve_by_telegram_user_id(tg_id)
    assert isinstance(ctx, EmployeeContext), f"подготовка: контекст сотрудника не собран ({ctx})"
    return ctx


def absence(employee, code, first, last, tz=TZ):
    kind, _ = AbsenceType.objects.get_or_create(
        organization_id=employee.organization_id, code=code,
        defaults={"name": code.title(), "is_paid": True, "requires_approval": True},
    )
    start = datetime.combine(first, time.min, tzinfo=tz).astimezone(dt_timezone.utc)
    end = (datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz)
           - timedelta(microseconds=1)).astimezone(dt_timezone.utc)
    origin = AbsenceRequest.objects.create(
        organization_id=employee.organization_id, employee=employee, absence_type=kind,
        request_kind="CREATE", requested_start_at=start, requested_end_at=end,
        status="APPROVED", submitted_at=start,
    )
    return EmployeeAbsence.objects.create(
        origin_request=origin, organization_id=employee.organization_id,
        employee=employee, absence_type=kind, start_at=start, end_at=end,
        start_date=first, end_date=last, status="ACTIVE",
    )



# --- эталоны: праздники офисов и перевод -------------------------------------

from django_tests.conftest import make_office  # noqa: E402
from humotech.schedules.models import CalendarException  # noqa: E402

THU, FRI, SAT = date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)
SEP1, SEP15, SEP16, SEP30 = date(2026, 9, 1), date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 30)


def workdays(first, last, skip=()):
    day, out = first, []
    while day <= last:
        if day.isoweekday() <= 5 and day not in skip:
            out.append(day)
        day += timedelta(days=1)
    return out


def holiday(organization, office, day, *, working=False):
    return CalendarException.objects.create(
        organization=organization, office=office, date=day,
        name="Перенос" if working else "Праздник офиса",
        exception_type="WORKING_WEEKEND" if working else "HOLIDAY",
        is_working_day=working,
    )


def two_offices_one_holiday(organization, region):
    """Офисы A и B одного региона; 10.09 (Чт) — праздник ТОЛЬКО офиса A.

    Оба сотрудника приходят Пн, Вт, Ср, Пт 7–11.09 и не приходят в Чт.
    Эталон по региону: рабочих дней 4 (A) + 5 (B) = 9, явка 8, пропуск 1 (у B).
    """
    office_a = make_office(organization, region, "S4A")
    office_b = make_office(organization, region, "S4B")
    holiday(organization, office_a, THU)
    people = []
    for office in (office_a, office_b):
        emp = person(organization, office)
        schedule(organization, emp)
        for day in (MON, TUE, date(2026, 9, 9), FRI):
            visit(emp, office, at(day, 9), at(day, 18))
        people.append(emp)
    return office_a, office_b, people


def test_known_C13_office_holiday_leaks_into_region_analytics(make_actor, organization, region):
    """Э-1 (C-13). Ожидается по региону: рабочих дней 9, пропусков 1.

    Контроль: отчёт по офису B (там праздника A нет) — пропуск 1.
    ОЖИДАЕМО ПАДАЕТ: metrics._exceptions (analytics/metrics.py:491-502) кладёт праздник
    офиса A в словарь без ключа офиса → у B Чт тоже выходной: 8 / 0.
    """
    office_a, office_b, _ = two_offices_one_holiday(organization, region)
    actor = make_actor(organization, permissions=ALL)
    service = AnalyticsService()

    only_b = service.office(actor, office_b.id, first=MON, last=FRI).totals
    assert only_b["missed_days"] == 1, f"контроль: офис B, пропуск 1, получено {only_b['missed_days']}"

    totals = service.region(actor, region.id, first=MON, last=FRI).totals
    got = (totals["expected_working_days"], totals["missed_days"])
    assert got == (9, 1), (
        f"ожидалось (9 рабочих дней, 1 пропуск) — праздник только офиса A, получено {got} (C-13)"
    )


def test_baseline_overview_region_respects_office_holiday(make_actor, organization, region):
    """Э-1 контроль. /analytics/overview (страница «Аналитика») считает верно: 8 из 9, пропуск 1."""
    two_offices_one_holiday(organization, region)
    actor = make_actor(organization, permissions=ALL)
    summary = OverviewService().overview(actor, first=MON, last=FRI, region_id=region.id)["summary"]
    got = (summary["attendance"]["numerator"], summary["attendance"]["denominator"], summary["missed_days"])
    assert got == (8, 9, 1), f"ожидалось (8, 9, 1), получено {got}"


def test_new_D06_working_weekend_is_not_a_working_day_in_analytics(
    make_actor, organization, office, telegram_settings,
):
    """Э-1б (D-06). Суббота 12.09 — рабочая по переносу (WORKING_WEEKEND, вся организация).
    Сотрудник с графиком Пн–Пт не пришёл.

    Правило: перенос делает день рабочим → 1 рабочий день, 1 пропуск.
    Контроль: Mini App (statistics) и состав дня CRM (presence = NOT_COME) так и считают.
    ОЖИДАЕМО ПАДАЕТ: metrics._plan_for (analytics/metrics.py:742) требует start_time у дня
    недели графика; у субботы его нет → 0 рабочих дней, 0 пропусков в /analytics и overview.
    """
    holiday(organization, None, SAT, working=True)
    emp = person(organization, office)
    schedule(organization, emp)
    actor = make_actor(organization, permissions=ALL)

    stat = statistics.for_period(context(emp, 777_403_001), SAT, SAT)
    state = AttendanceHrService().presence(actor, day=SAT, office_id=office.id).rows[0].state
    assert (stat.working_days, stat.missed_days, state) == (1, 1, "NOT_COME"), (
        f"контроль: Mini App и состав дня, получено {(stat.working_days, stat.missed_days, state)}"
    )
    totals = AnalyticsService().office(actor, office.id, first=SAT, last=SAT).totals
    got = (totals["expected_working_days"], totals["missed_days"])
    assert got == (1, 1), f"ожидалось (1, 1) — рабочая суббота по переносу, получено {got} (D-06)"


def transferred(organization, region):
    """Перевод в середине месяца.

    Офис A до 15.09 включительно, офис B с 16.09. 10.09 — праздник только офиса A.
    Приходы 09–18 во все рабочие дни, кроме 10.09: 10 дней в A, 11 дней в B.
    """
    office_a = make_office(organization, region, "S4TA")
    office_b = make_office(organization, region, "S4TB")
    holiday(organization, office_a, THU)
    emp = person(organization, office_a, valid_to=SEP15)
    EmployeeAssignment.objects.create(
        organization=organization, employee=emp, office=office_b,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=SEP16,
    )
    schedule(organization, emp)
    for day in workdays(SEP1, SEP15, skip=(THU,)):
        visit(emp, office_a, at(day, 9), at(day, 18))
    for day in workdays(SEP16, SEP30):
        visit(emp, office_b, at(day, 9), at(day, 18))
    return office_a, office_b, emp


def test_baseline_daily_journal_uses_the_office_of_each_day(make_actor, organization, region):
    """Э-6 контроль. Журнал карточки (hr.daily) берёт офис и календарь на каждый день:
    рабочих дней 21, 10.09 — выходной, 03.09 — офис A, 20.09+ — офис B."""
    office_a, office_b, emp = transferred(organization, region)
    actor = make_actor(organization, permissions=ALL)
    journal = AttendanceHrService().daily(actor, emp.id, first=SEP1, last=SEP30)
    rows = {r["day"]: r for r in journal["days"]}
    got = (journal["totals"]["working_days"], rows[THU]["state"],
           rows[date(2026, 9, 3)]["office_id"] == office_a.id,
           rows[date(2026, 9, 21)]["office_id"] == office_b.id)
    assert got == (21, "DAY_OFF", True, True), f"ожидалось (21, DAY_OFF, True, True), получено {got}"


def test_new_D02_selfservice_applies_new_office_calendar_to_days_before_transfer(
    organization, region, telegram_settings,
):
    """Э-6 (D-02). Статистика сотрудника за сентябрь (Mini App «Месяц», бот «За месяц»).

    Правило: календарь — офиса, где человек числился в этот день → 21 рабочий день, 0 пропусков.
    ОЖИДАЕМО ПАДАЕТ: statistics._calendar_exceptions (attendance/statistics.py:490-503) берёт
    context.office — текущий офис B — на весь период: 22 рабочих дня, «Пропущено: 1».
    """
    _, _, emp = transferred(organization, region)
    stat = statistics.for_period(context(emp, 777_403_002), SEP1, SEP30)
    assert stat.attended_days == 21, f"контроль: 21 день с отметками, получено {stat.attended_days}"
    got = (stat.working_days, stat.missed_days)
    assert got == (21, 0), (
        f"ожидалось (21, 0): 10.09 — праздник офиса A, где он тогда работал; получено {got} (D-02)"
    )


def test_known_B04_transfer_month_is_attributed_to_the_new_office(make_actor, organization, region):
    """Э-6б (B-04). Отчёты /analytics по офисам за сентябрь.

    Правило: день относится к офису назначения на этот день →
    офис A: 10 рабочих дней (1–15.09 без праздника), офис B: 11 (16–30.09).
    ОЖИДАЕМО ПАДАЕТ: состав берётся на последний день периода (metrics.py:372, 407-422):
    A — 0 дней (человек «исчез»), B — 22 дня (весь месяц, и праздник A не учтён).
    То же в /analytics/overview и в графике главной CRM.
    """
    office_a, office_b, _ = transferred(organization, region)
    actor = make_actor(organization, permissions=ALL)
    service = AnalyticsService()
    a = service.office(actor, office_a.id, first=SEP1, last=SEP30).totals["expected_working_days"]
    b = service.office(actor, office_b.id, first=SEP1, last=SEP30).totals["expected_working_days"]
    assert (a, b) == (10, 11), (
        f"ожидалось A=10, B=11 рабочих дней по офису назначения на день, получено A={a}, B={b} (B-04)"
    )
