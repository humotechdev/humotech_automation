"""Этап 4, норма дня, переработка и выезд: эталоны C-12, D-03, D-04.

Тесты test_known_* и test_new_* ОЖИДАЕМО ПАДАЮТ на текущем коде: это
доказательство расхождения. test_baseline_* — контроль подготовки, проходят.
Календарь: Пн 7.09.2026, Вт 8.09.2026; пояс офиса Asia/Dushanbe (UTC+5).
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




# --- эталоны: норма, переработка, выезд ---------------------------------------

from humotech.attendance.models import FieldWorkRequest  # noqa: E402
from humotech.reports.builder import ReportBuilderService, ReportSpec  # noqa: E402
from humotech.schedules.models import ScheduleBreak  # noqa: E402


def worktime(actor, office, first, last):
    """Конструктор отчётов «Рабочее время» — то, что CRM выгружает в xlsx."""
    spec = ReportSpec.build("worktime", date_from=first, date_to=last, office_ids=[office.id])
    report = ReportBuilderService().open(actor, spec, author="аудит этапа 4")
    rows = list(report.rows())
    return report, rows


def test_known_C12_analytics_day_norm_is_shift_length_not_weekly_norm(
    make_actor, organization, office, telegram_settings,
):
    """Э-3 (C-12). График 09:00–18:00, weekly_minutes = 2400 (40 ч / 5 дней), обеда нет.

    Правило (одно на проект, statistics.py:110-113): норма дня = 2400 / 5 = 480 мин = 28800 с.
    Отработано Пн 09:00–17:00 = 28800 с → недоработки нет.
    ОЖИДАЕМО ПАДАЕТ: analytics/metrics.py:745-747 берёт end − start = 32400 с.
    """
    emp = person(organization, office)
    schedule(organization, emp, weekly=2400)
    visit(emp, office, at(MON, 9), at(MON, 17))
    actor = make_actor(organization, permissions=ALL)

    norm = statistics.for_period(context(emp, 777_402_001), MON, MON).days[0].norm_seconds
    assert norm == 28800, f"контроль: норма Mini App должна быть 28800, получено {norm}"

    totals = AnalyticsService().office(actor, office.id, first=MON, last=MON).totals
    assert (totals["expected_seconds"], totals["undertime_seconds"]) == (28800, 0), (
        "ожидалось норма 28800 с и недоработка 0 по правилу weekly_minutes/рабочие дни "
        f"(как в Mini App), получено норма {totals['expected_seconds']}, "
        f"недоработка {totals['undertime_seconds']} (C-12)"
    )


def test_new_D03_report_builder_worktime_plan_is_a_third_norm(
    make_actor, organization, office, telegram_settings,
):
    """Э-3б (D-03). Та же смена без перерывов в графике.

    Ожидалось: «План» выгрузки = норме Mini App = 28800 с.
    ОЖИДАЕМО ПАДАЕТ: reports/builder.py:397-421 planned_seconds = конец − начало −
    неоплачиваемые перерывы = 32400 с; «Недостача» 1 ч у человека, выполнившего норму.
    """
    emp = person(organization, office)
    schedule(organization, emp, weekly=2400)
    visit(emp, office, at(MON, 9), at(MON, 17))
    actor = make_actor(organization, permissions=ALL)

    norm = statistics.for_period(context(emp, 777_402_002), MON, MON).days[0].norm_seconds
    _, rows = worktime(actor, office, MON, MON)
    assert len(rows) == 1, f"контроль: в выгрузке одна строка-день, получено {len(rows)}"
    got = (rows[0]["planned"], rows[0]["shortfall"])
    assert got == (norm, 0), (
        f"ожидалось План {norm} с и Недостача 0 (норма Mini App), получено {got} (D-03)"
    )


def test_baseline_report_builder_with_unpaid_lunch_matches_selfservice(
    make_actor, organization, office, telegram_settings,
):
    """Э-3в. Если в графике есть неоплачиваемый обед 13:00–14:00, план выгрузки = 28800 с.

    Совпадение случайное: оно держится только при обеде ровно 1 ч и 40 ч в неделю.
    """
    emp = person(organization, office)
    sched = schedule(organization, emp, weekly=2400)
    monday = ScheduleDay.objects.get(schedule=sched, weekday=1)
    ScheduleBreak.objects.create(schedule_day=monday, name="Обед",
                                 start_time=time(13), end_time=time(14), is_paid=False)
    visit(emp, office, at(MON, 9), at(MON, 17))
    actor = make_actor(organization, permissions=ALL)

    norm = statistics.for_period(context(emp, 777_402_003), MON, MON).days[0].norm_seconds
    _, rows = worktime(actor, office, MON, MON)
    assert rows[0]["planned"] == norm == 28800, (
        f"ожидалось 28800 с в выгрузке и в Mini App, получено {rows[0]['planned']} и {norm}"
    )


def test_known_C12_confirmed_field_work_day_shows_undertime(
    make_actor, make_user, organization, office,
):
    """Э-10 (C-12). Подтверждённый выезд во вторник; норма выезда = 28800 с (field_work.py:146).

    Правило: подтверждённый выезд засчитывает полную норму дня — недоработки нет.
    ОЖИДАЕМО ПАДАЕТ: аналитика ждёт 32400 с (end − start) → «недоработка» 3600 с.
    """
    emp = person(organization, office)
    schedule(organization, emp, weekly=2400)
    moment = at(TUE, 8)
    FieldWorkRequest.objects.create(
        organization=organization, employee=emp, office=office, date=TUE,
        status="CONFIRMED", scheduled_start=time(9), scheduled_end=time(18),
        norm_seconds=28800, requested_by_user=make_user(organization),
        requested_at=moment, responded_at=moment, expires_at=moment + timedelta(hours=1),
    )
    actor = make_actor(organization, permissions=ALL)

    totals = AnalyticsService().office(actor, office.id, first=TUE, last=TUE).totals
    assert (totals["field_work_days"], totals["attended_days"]) == (1, 1), (
        f"контроль: выезд засчитан днём явки, получено {totals['field_work_days']}/{totals['attended_days']}"
    )
    assert totals["undertime_seconds"] == 0, (
        "ожидалось 0 с недоработки за подтверждённый выезд (норма выезда = норма дня), "
        f"получено {totals['undertime_seconds']} при норме аналитики {totals['expected_seconds']} (C-12)"
    )


def test_new_D04_overtime_is_netted_across_people_and_counts_unscheduled(
    make_actor, organization, office,
):
    """Э-4 (D-04). Пн 7.09, норма 9 ч (09–18, 2700 мин/нед — нормы совпадают).

    А: 09–20 = 11 ч (+2 ч). Б: 09–16 = 7 ч (−2 ч). В: графика нет, 09–17 = 8 ч.
    Правило: переработка и недоработка считаются по человеку, люди без графика
    не входят ни в часы, ни в норму → переработка 7200 с, недоработка 7200 с.
    Контроль: выгрузка «Рабочее время» даёт ровно это.
    ОЖИДАЕМО ПАДАЕТ: analytics/metrics.py:656-657 — max(Σфакт − Σнорма, 0) по всей
    области; часы человека без графика добавлены в факт (:562-566) → 28800 / 0.
    """
    a, b, c = (person(organization, office) for _ in range(3))
    schedule(organization, a)
    schedule(organization, b)
    visit(a, office, at(MON, 9), at(MON, 20))
    visit(b, office, at(MON, 9), at(MON, 16))
    visit(c, office, at(MON, 9), at(MON, 17))
    actor = make_actor(organization, permissions=ALL)

    report, _ = worktime(actor, office, MON, MON)
    assert (report.sums["overtime"], report.sums["shortfall"]) == (7200, 7200), (
        f"контроль: выгрузка по людям, получено {report.sums}"
    )
    totals = AnalyticsService().office(actor, office.id, first=MON, last=MON).totals
    got = (totals["overtime_seconds"], totals["undertime_seconds"])
    assert got == (7200, 7200), (
        f"ожидалось (7200, 7200) по правилу «по человеку, без людей без графика», получено {got}; "
        f"факт {totals['worked_seconds']} с, норма {totals['expected_seconds']} с (D-04)"
    )


def test_new_D04_overtime_netted_across_days_needs_one_rule(make_actor, organization, office):
    """Э-4б (D-04, требует решения). Один человек: Пн 10 ч (+1), Вт 8 ч (−1), норма 9 ч.

    Вариант А (табель по дням): переработка 3600, недоработка 3600 — так считает выгрузка.
    Вариант Б (суммированный учёт за период): 0 / 0 — так считает /analytics.
    Тест требует одинакового ответа в двух местах; ОЖИДАЕМО ПАДАЕТ, пока правило не выбрано.
    """
    emp = person(organization, office)
    schedule(organization, emp)
    visit(emp, office, at(MON, 9), at(MON, 19))
    visit(emp, office, at(TUE, 9), at(TUE, 17))
    actor = make_actor(organization, permissions=ALL)

    report, _ = worktime(actor, office, MON, TUE)
    builder = (report.sums["overtime"], report.sums["shortfall"])
    totals = AnalyticsService().office(actor, office.id, first=MON, last=TUE).totals
    analytics = (totals["overtime_seconds"], totals["undertime_seconds"])
    assert builder == (3600, 3600), f"контроль: выгрузка по дням, получено {builder}"
    assert analytics == builder, (
        f"переработка/недоработка расходятся: /analytics {analytics}, выгрузка {builder} (D-04)"
    )
