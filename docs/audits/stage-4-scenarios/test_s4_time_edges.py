"""Этап 4, время: будущие дни, незакрытая сессия, ночная смена, правка графика
задним числом, многопоясной регион, выходной на главной: D-01, D-05, C-07, C-10, C-06, D-07.

test_known_*/test_new_* ОЖИДАЕМО ПАДАЮТ (доказательство), test_baseline_* проходят.
Часы не подменяются: «сейчас» передаётся параметром now, будущее берётся от
настоящей даты.
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



# --- эталоны: будущие дни, незакрытая сессия, ночь, правка задним числом --------

from django.utils import timezone  # noqa: E402

from django_tests.conftest import make_office  # noqa: E402
from humotech.analytics.dashboard import DashboardService  # noqa: E402

WED, THU, FRI, SAT = date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)


def next_week():
    """Пн–Пт следующей недели относительно настоящего «сегодня» в поясе офиса."""
    today = timezone.now().astimezone(TZ).date()
    monday = today + timedelta(days=7 - today.weekday())
    return monday, monday + timedelta(days=4)


def test_new_D01_future_working_days_are_reported_as_missed_to_employee(
    organization, office, telegram_settings,
):
    """Э-13 (D-01). /me/statistics за неделю, которая ещё не наступила (то же — хвост
    текущего месяца в «За месяц» бота и «Месяц» Mini App).

    Правило: день, который не наступил, — не пропуск → «Пропущено: 0».
    ОЖИДАЕМО ПАДАЕТ: DayRecord.missed (attendance/statistics.py:121-128) не знает «сейчас» → 5.
    """
    emp = person(organization, office)
    schedule(organization, emp)
    first, last = next_week()
    stat = statistics.for_period(context(emp, 777_405_001), first, last)
    assert stat.working_days == 5, f"контроль: 5 рабочих дней, получено {stat.working_days}"
    assert stat.missed_days == 0, (
        f"ожидалось 0 пропусков за будущие дни {first}…{last}, получено {stat.missed_days} (D-01)"
    )


def test_new_D01_week_in_progress_counts_today_and_the_rest_as_missed(
    organization, office, telegram_settings,
):
    """Э-13б (D-01). «Сейчас» = Ср 9.09 10:00 (параметр now, без подмены часов).
    Пн и Вт отработаны, в среду человек ещё может прийти (смена до 18:00), Чт и Пт впереди.

    Правило: «Пропущено» = 0. Контроль: главный экран (current_status) НЕ объявляет
    среду пропуском (OUTSIDE). ОЖИДАЕМО ПАДАЕТ: бот «За неделю» покажет «Пропущено: 3».
    """
    emp = person(organization, office)
    schedule(organization, emp)
    for day in (MON, TUE):
        visit(emp, office, at(day, 9), at(day, 18))
    ctx = context(emp, 777_405_002)
    now = at(WED, 10)
    state = statistics.current_status(ctx, now=now).state
    assert state == "OUTSIDE", f"контроль: в 10:00 среда ещё не пропуск, получено {state}"
    week = statistics.for_week(ctx, now=now)
    assert (week.first, week.attended_days, week.missed_days) == (MON, 2, 0), (
        f"ожидалось (неделя с {MON}, 2 дня с отметками, 0 пропусков), получено "
        f"({week.first}, {week.attended_days}, {week.missed_days}) (D-01)"
    )


def test_new_D01_analytics_api_counts_future_days_as_missed(make_actor, organization, office):
    """Э-13в (D-01). /analytics (и «Сводка по офисам», sheets.py:294-299, по умолчанию — текущий месяц).

    Контроль: /analytics/overview исключает будущие дни (overview.py:271-272) → 0.
    ОЖИДАЕМО ПАДАЕТ: AnalyticsService._compute идёт по всем дням периода → 5 пропусков, явка 0 %.
    """
    emp = person(organization, office)
    schedule(organization, emp)
    first, last = next_week()
    actor = make_actor(organization, permissions=ALL)
    overview = OverviewService().overview(actor, first=first, last=last, office_id=office.id)["summary"]
    assert overview["missed_days"] == 0, f"контроль: overview, получено {overview['missed_days']}"
    totals = AnalyticsService().office(actor, office.id, first=first, last=last).totals
    got = (totals["expected_working_days"], totals["missed_days"])
    assert got == (0, 0), f"ожидалось (0, 0) для будущей недели, получено {got} (D-01)"


def test_new_D05_open_session_hours_differ_between_employee_and_crm(
    make_actor, organization, office, telegram_settings,
):
    """Э-14 (D-05, требует решения). Вход Пн 7.09 09:00, выхода нет; «сейчас» = Чт 10.09 09:00.

    Варианты правила: А) 0 ч + пометка «не закрыта» (так CRM, /analytics, выгрузки);
    Б) до конца смены по графику (9 ч) с пометкой; В) до «сейчас» (так Mini App и бот: 72 ч).
    Любой вариант требует ОДНОГО числа в Mini App и в CRM. ОЖИДАЕМО ПАДАЕТ: 259200 против 0.
    """
    emp = person(organization, office)
    schedule(organization, emp)
    visit(emp, office, at(MON, 9))
    actor = make_actor(organization, permissions=ALL)
    stat = statistics.for_period(context(emp, 777_405_003), MON, MON, now=at(THU, 9))
    totals = AnalyticsService().office(actor, office.id, first=MON, last=MON).totals
    journal = AttendanceHrService().daily(actor, emp.id, first=MON, last=MON)["days"][0]
    assert totals["open_sessions"] == 1, f"контроль: 1 незакрытая сессия, получено {totals['open_sessions']}"
    got = {"Mini App/бот": stat.seconds, "/analytics": totals["worked_seconds"],
           "карточка CRM": journal["seconds"]}
    assert len(set(got.values())) == 1, f"часы незакрытой сессии расходятся: {got} (D-05)"


def test_known_C07_night_shift_entry_after_midnight_is_lost(
    make_actor, organization, office, telegram_settings,
):
    """Э-5б (C-07, требует решения В-6). Смена Пн 22:00 – Вт 06:00; пришёл Вт 00:20, ушёл 06:00.

    Правило (привязка к смене): смена понедельника отработана, опоздание 140 мин, не пропуск.
    ОЖИДАЕМО ПАДАЕТ: сессия относится к дню начала (вторник) → Пн: явка 0, пропуск 1.
    """
    emp = person(organization, office)
    schedule(organization, emp, start=time(22), end=time(6), weekly=2400, crosses=True)
    visit(emp, office, at(TUE, 0, 20), at(TUE, 6))
    actor = make_actor(organization, permissions=ALL)
    seconds = statistics.for_period(context(emp, 777_405_004), TUE, TUE).seconds
    assert seconds == 20400, f"контроль: 5 ч 40 мин во вторнике, получено {seconds}"
    totals = AnalyticsService().office(actor, office.id, first=MON, last=MON).totals
    got = (totals["attended_days"], totals["missed_days"], totals["late_minutes_total"])
    assert got == (1, 0, 140), f"ожидалось (1, 0, 140) за смену Пн, получено {got} (C-07)"


def test_known_C10_schedule_edit_rewrites_past_lateness(make_actor, organization, office):
    """Э-15 (C-10). Начало 09:00, допуск 0; приходы Пн–Пт 7–11.09 в 09:20 → 5 × 20 = 100 мин.
    В октябре HR меняет начало на 08:30 (правка дней графика на месте, как schedules/services.py:189-260).

    Правило: закрытый период не пересчитывается → опоздания за 7–11.09 остаются 100 мин.
    ОЖИДАЕМО ПАДАЕТ: 250 мин (5 × 50) — история переписана.
    """
    emp = person(organization, office)
    sched = schedule(organization, emp)
    for day in (MON, TUE, WED, THU, FRI):
        visit(emp, office, at(day, 9, 20), at(day, 18))
    actor = make_actor(organization, permissions=ALL)
    service = AnalyticsService()
    before = service.office(actor, office.id, first=MON, last=FRI).totals["late_minutes_total"]
    assert before == 100, f"контроль: до правки 100 мин, получено {before}"
    ScheduleDay.objects.filter(schedule=sched, is_working_day=True).update(start_time=time(8, 30))
    after = service.office(actor, office.id, first=MON, last=FRI).totals["late_minutes_total"]
    assert after == 100, f"ожидалось 100 мин за сентябрь после октябрьской правки, получено {after} (C-10)"


def test_known_C06_region_report_uses_the_first_office_timezone(make_actor, organization, region):
    """Э-12б (C-06). Регион: «Офис AMSK» (Europe/Moscow, UTC+3, первый по имени) и
    «Офис TASH» (Asia/Tashkent, UTC+5). Сотрудник TASH пришёл Вт 10:00 по Ташкенту при начале 09:00.

    Правило: опоздание в поясе своего офиса = 60 мин.
    Контроль: отчёт по офису TASH и /analytics/overview по региону — 60.
    ОЖИДАЕМО ПАДАЕТ: /analytics?region_id берёт пояс первого офиса (metrics.py:360) → 08:00, опоздания нет.
    """
    msk = make_office(organization, region, "AMSK")
    msk.timezone = "Europe/Moscow"
    msk.save(update_fields=["timezone"])
    tash_office = make_office(organization, region, "TASH")
    tash_office.timezone = "Asia/Tashkent"
    tash_office.save(update_fields=["timezone"])
    tash = ZoneInfo("Asia/Tashkent")
    emp = person(organization, tash_office)
    schedule(organization, emp)
    visit(emp, tash_office, at(TUE, 10, 0, tash), at(TUE, 18, 0, tash))
    actor = make_actor(organization, permissions=ALL)
    service = AnalyticsService()
    office_only = service.office(actor, tash_office.id, first=TUE, last=TUE).totals["late_minutes_total"]
    people = OverviewService().overview(actor, first=TUE, last=TUE, region_id=region.id)["employees"]
    assert (office_only, people[0]["late_minutes"]) == (60, 60), (
        f"контроль: офис и overview, получено {(office_only, people[0]['late_minutes'])}"
    )
    regional = service.region(actor, region.id, first=TUE, last=TUE).totals["late_minutes_total"]
    assert regional == 60, f"ожидалось 60 мин в поясе офиса TASH, получено {regional} (C-06)"


def test_new_D07_dashboard_should_work_counts_day_off_and_unscheduled_arrivals(
    make_actor, organization, office,
):
    """Э-16 (D-07). Суббота 12.09. Сотрудник с графиком Пн–Пт и сотрудник без графика
    пришли 10:00–14:00. По графику в субботу не ждали никого.

    Правило: «Должны работать сегодня» — по графику и календарю → 0; «Пришли» = 2.
    Контроль: /analytics/overview — 0 ожидаемых.
    ОЖИДАЕМО ПАДАЕТ: analytics/dashboard.py:116-120 складывает «пришли» в «должны» → 2,
    и экран «Посещаемость» CRM показывает явку 100 % (AttendancePage.tsx:347).
    """
    scheduled = person(organization, office)
    schedule(organization, scheduled)
    unscheduled = person(organization, office)
    for emp in (scheduled, unscheduled):
        visit(emp, office, at(SAT, 10), at(SAT, 14))
    actor = make_actor(organization, permissions=ALL)
    cards = {c.key: c.value for c in DashboardService().summary(actor, day=SAT, office_id=office.id).cards}
    days = OverviewService().overview(actor, first=SAT, last=SAT, office_id=office.id)["days"]
    assert (cards["came"], days[0]["expected"]) == (2, 0), (
        f"контроль: пришли 2, overview ждал 0, получено {(cards['came'], days[0]['expected'])}"
    )
    assert cards["should_work_today"] == 0, (
        f"ожидалось 0 «должны работать» в выходной, получено {cards['should_work_today']} (D-07)"
    )


def test_baseline_XDAY01_scan_day_follows_the_office_zone(organization, region):
    """Э-12в (X-DAY-01 / C-05, T-11 этапа 3б). Перевод в офис B (Asia/Tashkent) с 16.09.

    Правило: «день» отметки — по поясу офиса. 16.09 00:30 по Ташкенту (= 15.09 19:30Z)
    уже день нового назначения → отметка в B разрешена; 15.09 23:30 — ещё нет.
    В рабочей копии исправлено (attendance/services.py:120-128, local_date по поясу офиса),
    тест должен ПРОЙТИ и закрепить исправление. Остальные места X-DAY-01 (hr.py:284,
    analytics/views.py _period, people.py:97, sheets.py:298, 536) без подмены часов не проверить.
    """
    from humotech.attendance.services import employee_may_use_office

    office_a = make_office(organization, region, "XDA")
    office_b = make_office(organization, region, "XDB")
    office_b.timezone = "Asia/Tashkent"
    office_b.save(update_fields=["timezone"])
    tash = ZoneInfo("Asia/Tashkent")
    sep15, sep16 = date(2026, 9, 15), date(2026, 9, 16)
    emp = person(organization, office_a, valid_to=sep15)
    EmployeeAssignment.objects.create(
        organization=organization, employee=emp, office=office_b,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True, valid_from=sep16,
    )
    allowed = {
        "16.09 10:00": employee_may_use_office(employee_id=emp.id, office_id=office_b.id,
                                               at=at(sep16, 10, 0, tash)),
        "16.09 00:30": employee_may_use_office(employee_id=emp.id, office_id=office_b.id,
                                               at=at(sep16, 0, 30, tash)),
        "15.09 23:30": employee_may_use_office(employee_id=emp.id, office_id=office_b.id,
                                               at=at(sep15, 23, 30, tash)),
    }
    assert allowed == {"16.09 10:00": True, "16.09 00:30": True, "15.09 23:30": False}, (
        f"ожидалось: 16.09 в любое время — да, 15.09 — нет (пояс офиса); получено {allowed}"
    )
