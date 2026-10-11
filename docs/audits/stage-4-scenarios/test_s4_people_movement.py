"""Этап 4, численность, увольнение и движение: B-04, D-08.

test_known_*/test_new_* ОЖИДАЕМО ПАДАЮТ (доказательство), test_baseline_* проходят.
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



# --- эталоны: численность, увольнение, движение -------------------------------

from django_tests.conftest import make_office  # noqa: E402
from humotech.analytics.movement import MovementService  # noqa: E402
from humotech.analytics.people import PeopleService  # noqa: E402
from humotech.reports.builder import ReportBuilderService, ReportSpec  # noqa: E402

AUG1, AUG31 = date(2026, 8, 1), date(2026, 8, 31)
SEP1, SEP15, SEP16, SEP30 = date(2026, 9, 1), date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 30)


def workdays(first, last, skip=()):
    day, out = first, []
    while day <= last:
        if day.isoweekday() <= 5 and day not in skip:
            out.append(day)
        day += timedelta(days=1)
    return out


def quit_mid_month(organization, office):
    """П работает весь сентябрь (22 рабочих дня, все с отметками).
    Т уволен 15.09 (статус TERMINATED, назначение по 15.09), отметки 1–15.09 (11 дней)."""
    stayed = person(organization, office)
    left = person(organization, office, valid_to=SEP15, status="TERMINATED", termination=SEP15)
    for emp in (stayed, left):
        schedule(organization, emp)
    for day in workdays(SEP1, SEP30):
        visit(stayed, office, at(day, 9), at(day, 18))
    for day in workdays(SEP1, SEP15):
        visit(left, office, at(day, 9), at(day, 18))
    return stayed, left


def test_known_B04_terminated_employee_disappears_from_past_period(make_actor, organization, office):
    """Э-7 (B-04). Явка офиса за сентябрь, отчёт строится после увольнения.

    Правило: прошлые дни уволенного остаются в периоде → рабочих 22 + 11 = 33, явка 33.
    ОЖИДАЕМО ПАДАЕТ: roster_assignment_filter(last) + текущий статус (metrics.py:372, 415-419):
    уволенного нет вовсе → 22 / 22. Тот же эффект в /analytics/overview.
    """
    quit_mid_month(organization, office)
    actor = make_actor(organization, permissions=ALL)
    totals = AnalyticsService().office(actor, office.id, first=SEP1, last=SEP30).totals
    assert totals["attended_days"] >= 22, f"контроль: П учтён, получено {totals['attended_days']}"
    got = (totals["expected_working_days"], totals["attended_days"])
    assert got == (33, 33), f"ожидалось (33, 33) с днями уволенного 1–15.09, получено {got} (B-04)"


def test_baseline_termination_is_counted_once_in_team_and_movement(make_actor, organization, office):
    """Э-7 контроль. «Ушли за период» = 1 и в «Команде» (страница Аналитика), и в /analytics/movement."""
    quit_mid_month(organization, office)
    actor = make_actor(organization, permissions=ALL)
    team = PeopleService().team(actor, first=SEP1, last=SEP30, office_id=office.id)["summary"]
    movement = MovementService().report(actor, first=SEP1, last=SEP30, office_id=office.id)
    got = (team["left"], movement.current.left, team["headcount"], movement.headcount)
    assert got == (1, 1, 1, 1), f"ожидалось (1, 1, 1, 1), получено {got}"


def test_baseline_report_builder_with_inactive_keeps_days_before_termination(
    make_actor, organization, office,
):
    """Э-7 контроль. Выгрузка «Посещаемость» с include_inactive=True: у Т 11 дней с отметками.

    Внимание: CRM заказывает выгрузки с include_inactive=false (AnalyticsPage.tsx:372-377) —
    тогда Т пропадает из сентябрьского файла целиком (см. отчёт, B-04).
    """
    _, left = quit_mid_month(organization, office)
    actor = make_actor(organization, permissions=ALL)
    spec = ReportSpec.build("attendance", date_from=SEP1, date_to=SEP30,
                            office_ids=[office.id], include_inactive=True)
    report = ReportBuilderService().open(actor, spec, author="аудит этапа 4")
    list(report.rows())
    present = report.people.get(left.id, {}).get("present")
    assert present == 11, f"ожидалось 11 дней с отметками у уволенного 15.09, получено {present}"


def test_new_D08_movement_headcount_of_past_date_uses_current_status(make_actor, organization, office):
    """Э-11 (D-08). Численность офиса на 31.08 (до увольнения Т 15.09) = 2.

    Контроль: «Команда» (PeopleService.team) — 2.
    ОЖИДАЕМО ПАДАЕТ: MovementService._headcount (analytics/movement.py:113-127) фильтрует по
    ТЕКУЩЕМУ employment_status → Т не считается и в августе: 1.
    """
    quit_mid_month(organization, office)
    actor = make_actor(organization, permissions=ALL)
    team = PeopleService().team(actor, first=AUG1, last=AUG31, office_id=office.id)["summary"]["headcount"]
    assert team == 2, f"контроль: численность «Команды» на 31.08 = 2, получено {team}"
    movement = MovementService().report(actor, first=AUG1, last=AUG31, office_id=office.id).headcount
    assert movement == 2, (
        f"ожидалось 2 человека на 31.08 (Т уволен позже), получено {movement} (D-08)"
    )


def test_new_D08_movement_counts_anyone_ever_assigned_to_the_office(make_actor, organization, region):
    """Э-11б (D-08). Офис A: П работает весь период; Р переведён из A в B с 16.09.

    Правило: численность офиса A на 30.09 = 1; A + B = организация = 2.
    Контроль: «Команда» по офису A — 1.
    ОЖИДАЕМО ПАДАЕТ: MovementService._scope (movement.py:129-150) — все, у кого КОГДА-ЛИБО было
    основное назначение в A → A = 2, B = 1, сумма 3 > 2.
    """
    office_a = make_office(organization, region, "S4MA")
    office_b = make_office(organization, region, "S4MB")
    person(organization, office_a)
    moved = person(organization, office_a, valid_to=SEP15)
    EmployeeAssignment.objects.create(
        organization=organization, employee=moved, office=office_b,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True, valid_from=SEP16,
    )
    actor = make_actor(organization, permissions=ALL)
    team = PeopleService().team(actor, first=SEP1, last=SEP30, office_id=office_a.id)["summary"]["headcount"]
    assert team == 1, f"контроль: «Команда» офиса A на 30.09 = 1, получено {team}"
    service = MovementService()
    a = service.report(actor, first=SEP1, last=SEP30, office_id=office_a.id).headcount
    b = service.report(actor, first=SEP1, last=SEP30, office_id=office_b.id).headcount
    total = service.report(actor, first=SEP1, last=SEP30).headcount
    assert (a, b, total) == (1, 1, 2), (
        f"ожидалось A=1, B=1, всего 2, получено A={a}, B={b}, всего {total} (D-08)"
    )
