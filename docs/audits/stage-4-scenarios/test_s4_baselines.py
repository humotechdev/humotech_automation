"""Этап 4, эталоны-«контроли»: показатели, которые ДОЛЖНЫ совпадать везде.

Все тесты этого файла ожидаемо ПРОХОДЯТ на текущем коде. Они нужны, чтобы
отличать «найдена ошибка» от «сломана подготовка данных» в соседних
файлах test_s4_*: те же помощники, тот же календарь, те же сервисы.

Календарь: сентябрь 2026, 1 сентября — вторник. Пояс офиса — Asia/Dushanbe
(UTC+5), если не сказано иное. Даты в прошлом относительно 11.10.2026,
поэтому «сегодня» сервера на результат не влияет.
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


# --- эталоны -----------------------------------------------------------------

def test_baseline_0030_tashkent_belongs_to_the_new_local_day(
    make_actor, organization, office, telegram_settings,
):
    """Э-12. Отметка 00:30 по Ташкенту (UTC+5) = 19:30Z накануне.

    Правило: сутки — в поясе офиса; сессия принадлежит дню начала.
    Ожидается: 7.09 — 0 с, 8.09 — 7200 с во всех трёх расчётах.
    """
    office.timezone = "Asia/Tashkent"
    office.save(update_fields=["timezone"])
    tash = ZoneInfo("Asia/Tashkent")
    emp = person(organization, office)
    schedule(organization, emp)
    visit(emp, office, at(TUE, 0, 30, tash), at(TUE, 2, 30, tash))
    actor = make_actor(organization, permissions=ALL)

    stat = {d.day: d.seconds for d in statistics.for_period(context(emp, 777_401_001), MON, TUE).days}
    series = {p.day: p.worked_seconds for p in
              AnalyticsService().office(actor, office.id, first=MON, last=TUE).series}
    daily = {r["day"]: r["seconds"] for r in
             AttendanceHrService().daily(actor, emp.id, first=MON, last=TUE)["days"]}
    expected = {MON: 0, TUE: 7200}
    assert stat == expected, f"statistics (Mini App/бот): ожидалось {expected}, получено {stat}"
    assert series == expected, f"analytics series: ожидалось {expected}, получено {series}"
    assert daily == expected, f"hr.daily (карточка CRM): ожидалось {expected}, получено {daily}"


def test_baseline_late_minutes_over_grace_are_the_same_everywhere(
    make_actor, organization, office,
):
    """Э-2. Допуск 10 мин. Пн приход 09:25 → 15 мин сверх допуска; Вт 09:08 → вовремя.

    Правило: опоздание = max(приход − начало смены − допуск, 0), минуты вниз.
    Ожидается: 1 опоздание, 15 минут — в /analytics, /analytics/overview, журнале.
    """
    emp = person(organization, office)
    schedule(organization, emp, grace=10)
    visit(emp, office, at(MON, 9, 25), at(MON, 18))
    visit(emp, office, at(TUE, 9, 8), at(TUE, 18))
    actor = make_actor(organization, permissions=ALL)

    totals = AnalyticsService().office(actor, office.id, first=MON, last=TUE).totals
    people = OverviewService().overview(actor, first=MON, last=TUE, office_id=office.id)["employees"]
    journal = AttendanceHrService().daily(actor, emp.id, first=MON, last=TUE)["totals"]

    got = {
        "analytics": (totals["late_arrivals"], totals["late_minutes_total"]),
        "overview": (people[0]["late_days"], people[0]["late_minutes"]),
        "daily": (journal["late_days"], journal["late_minutes"]),
    }
    assert set(got.values()) == {(1, 15)}, (
        f"ожидалось (1 опоздание, 15 мин) по правилу «минуты сверх допуска», получено {got}"
    )


def test_baseline_review_mark_is_counted_the_same_everywhere(
    make_actor, organization, office, telegram_settings,
):
    """Э-9. Вход со статусом REVIEW (подозрение на общий код), выход ACCEPTED.

    Правило сейчас: REVIEW засчитывается как отметка (COUNTED_VERIFICATION).
    Требует решения (В-3 / C-03): засчитывать до разбора HR или нет.
    Здесь проверяется только согласованность: 1 день и 9 ч во всех расчётах.
    """
    emp = person(organization, office)
    schedule(organization, emp)
    visit(emp, office, at(MON, 9), at(MON, 18), verification="REVIEW")
    actor = make_actor(organization, permissions=ALL)

    stat = statistics.for_period(context(emp, 777_401_002), MON, MON)
    totals = AnalyticsService().office(actor, office.id, first=MON, last=MON).totals
    row = AttendanceHrService().presence(actor, day=MON, office_id=office.id).rows[0]
    got = {
        "statistics": (stat.attended_days, stat.seconds),
        "analytics": (totals["attended_days"], totals["worked_seconds"]),
        "presence": (int(row.state == "LEFT"), row.seconds),
    }
    assert set(got.values()) == {(1, 32400)}, f"REVIEW-отметка посчитана по-разному: {got}"


def test_baseline_night_shift_started_before_midnight(make_actor, organization, office,
                                                      telegram_settings):
    """Э-5а. Смена 22:00–06:00 (Пн–Пт). Вход Пн 22:10, выход Вт 06:00.

    Ожидается: день Пн отработан, опоздание 10 мин, 7 ч 50 мин = 28200 с.
    """
    emp = person(organization, office)
    schedule(organization, emp, start=time(22), end=time(6), weekly=2400, crosses=True)
    visit(emp, office, at(MON, 22, 10), at(TUE, 6))
    actor = make_actor(organization, permissions=ALL)

    totals = AnalyticsService().office(actor, office.id, first=MON, last=MON).totals
    stat = statistics.for_period(context(emp, 777_401_003), MON, MON)
    journal = AttendanceHrService().daily(actor, emp.id, first=MON, last=MON)["days"][0]
    got = (totals["attended_days"], totals["late_minutes_total"], totals["worked_seconds"],
           stat.seconds, journal["late_minutes"])
    assert got == (1, 10, 28200, 28200, 10), (
        "ожидалось (явка 1, опоздание 10, 28200 с в аналитике, 28200 с в статистике, "
        f"10 мин в журнале), получено {got}"
    )


def test_baseline_sick_leave_inside_vacation_cannot_be_stored(organization, office):
    """Э-8. Больничный 16–17.09 внутри отпуска 14–25.09.

    Факт (C-18): оформить нельзя даже на уровне базы — ограничение
    ex_employee_absences_overlap. Поэтому эталон «больничный прерывает отпуск
    и возвращает N дней» сейчас проверить невозможно; правило — за владельцем.
    """
    emp = person(organization, office)
    absence(emp, "ANNUAL_LEAVE", date(2026, 9, 14), date(2026, 9, 25))
    with pytest.raises(IntegrityError) as caught, transaction.atomic():
        absence(emp, "SICK_LEAVE", date(2026, 9, 16), date(2026, 9, 17))
    assert "ex_employee_absences_overlap" in str(caught.value), (
        f"ожидался отказ ограничения пересечения отсутствий, получено: {caught.value}"
    )
