"""Этап 4, отсутствия и остаток отпуска: эталон дней отпуска и D-09.

test_new_* ОЖИДАЕМО ПАДАЕТ, test_baseline_* проходит.
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



# --- эталоны: отсутствия и остаток отпуска --------------------------------------

from django.utils import timezone  # noqa: E402

from humotech.absences.models import LeaveBalance  # noqa: E402
from humotech.absences.services import AbsenceService  # noqa: E402
from humotech.ai_assistant.services.personal_data_service import (  # noqa: E402
    SqlPersonalDataQueryService,
)
from humotech.selfservice.absences import _days as selfservice_days  # noqa: E402

SEP14, SEP20 = date(2026, 9, 14), date(2026, 9, 20)


def test_baseline_vacation_days_are_calendar_days_on_every_surface(
    make_actor, organization, office, telegram_settings,
):
    """Э-17. Отпуск Пн 14.09 – Вс 20.09 (7 календарных, 5 рабочих дней).

    Сейчас все три расчёта считают «дни отпуска» календарными: 7; рабочие дни отпуска
    не попадают ни в «рабочие по графику» аналитики, ни в пропуски.
    Рядом заявка в боте показывает «рабочих дней: 5» — см. отчёт (D-10, требует решения).
    """
    emp = person(organization, office)
    schedule(organization, emp)
    absence(emp, "ANNUAL_LEAVE", SEP14, SEP20)
    actor = make_actor(organization, permissions=ALL)

    stat = statistics.for_period(context(emp, 777_406_001), SEP14, SEP20)
    totals = AnalyticsService().office(actor, office.id, first=SEP14, last=SEP20).totals
    summary = OverviewService().overview(actor, first=SEP14, last=SEP20, office_id=office.id)["summary"]
    got = {
        "statistics": (stat.vacation_days, stat.missed_days),
        "analytics": (totals["vacation_days"], totals["missed_days"]),
        "overview": (summary["vacation_days"], summary["missed_days"]),
    }
    assert set(got.values()) == {(7, 0)}, f"ожидалось (7 дней отпуска, 0 пропусков) везде, получено {got}"


def test_new_D09_leave_balance_in_days_differs_between_mini_app_and_assistant(
    organization, office, telegram_settings,
):
    """Э-18 (D-09). Неполный день: график 4 ч × 5 дней (weekly 1200). Начислено 2400 мин.

    Правило: «остаток в днях» один для всех каналов (какой именно — В-9 / C-28:
    по 480 мин или по норме дня графика).
    Mini App/бот (selfservice/absences.py:543-544): 2400 / 480 = 5.0 дн.
    AI-ассистент (personal_data_service.py:496-499): 2400 / 240 = 10.0 дн.
    ОЖИДАЕМО ПАДАЕТ: 5.0 против 10.0. Сейчас латентно: балансов в рабочей базе нет (A-15).
    """
    emp = person(organization, office)
    schedule(organization, emp, start=time(9), end=time(13), weekly=1200)
    kind, _ = AbsenceType.objects.get_or_create(
        organization=organization, code="ANNUAL_LEAVE",
        defaults={"name": "Ежегодный отпуск", "is_paid": True, "requires_approval": True,
                  "deducts_leave_balance": True},
    )
    year = timezone.now().astimezone(TZ).year
    LeaveBalance.objects.create(
        organization=organization, employee=emp, absence_type=kind, year=year,
        allocated_minutes=2400, used_minutes=0, reserved_minutes=0, adjustment_minutes=0,
    )
    rows = AbsenceService().balances(context(emp, 777_406_002), year=year)
    assert len(rows) == 1, f"контроль: один баланс, получено {len(rows)}"
    mini_app = selfservice_days(rows[0].available_minutes)
    result = SqlPersonalDataQueryService()._leave_balance(emp, TZ, timezone.now())
    assistant = result.data.get("available_days")
    assert mini_app == assistant, (
        f"остаток отпуска в днях расходится: Mini App {mini_app}, ассистент {assistant} (D-09)"
    )
