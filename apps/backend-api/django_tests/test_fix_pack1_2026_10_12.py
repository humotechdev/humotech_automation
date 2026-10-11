"""Первый пакет бесспорных технических исправлений (разрешение владельца 12.10.2026).

Каждый тест написан до исправления и на прежнем коде падает. Пункты:
время офиса (X-DAY-01, C-04, C-26), праздник офиса в аналитике (C-13),
аудитории опросов (A-10/B-03), приветствие при приёме (B-02), блокировки
привязки Telegram (A-25), минимальный мониторинг (A-42).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from rest_framework.test import APIClient

from django_tests.conftest import create_actor, make_office
from humotech.employees.models import Employee, EmployeeAssignment

pytestmark = pytest.mark.django_db

API = "/api/v1"
UTC = dt_timezone.utc


def _employee(organization, office, number, *, valid_from=date(2024, 1, 1), valid_to=None):
    emp = Employee.objects.create(
        organization=organization, employee_number=number,
        first_name="Тест", last_name="Пакет", hire_date=date(2024, 1, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=emp, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=valid_from, valid_to=valid_to,
    )
    return emp


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# =============================================================================
# 1. Время офиса
# =============================================================================


class TestOfficeTime:

    def test_scan_after_local_midnight_on_transfer_day(self, organization, region):
        """A-08: перевод с 13.10; в 02:00 13.10 по поясу офиса (21:00Z 12.10)
        сотрудник уже работает в новом офисе — допуск по МЕСТНОЙ дате."""
        from humotech.attendance.services import employee_may_use_office

        old = make_office(organization, region, "T1-OLD")
        new = make_office(organization, region, "T1-NEW")
        tz = ZoneInfo(new.timezone)
        emp = _employee(organization, old, "T1-E", valid_to=date(2026, 10, 12))
        EmployeeAssignment.objects.create(
            organization=organization, employee=emp, office=new,
            employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
            valid_from=date(2026, 10, 13),
        )
        # В бою сюда приходит timezone.now() — момент в UTC.
        at = datetime(2026, 10, 13, 2, 0, tzinfo=tz).astimezone(UTC)
        assert at.date() == date(2026, 10, 12)  # в UTC ещё вчера
        assert employee_may_use_office(employee_id=emp.id, office_id=new.id, at=at)
        assert not employee_may_use_office(employee_id=emp.id, office_id=old.id, at=at)

    def test_manual_mark_without_offset_is_office_wall_clock(self, organization, office):
        """C-04: CRM шлёт «09:00» без смещения с подписью «время офиса» —
        это 09:00 по поясу офиса, а не 09:00 UTC."""
        from humotech.attendance.models import AttendanceEvent

        emp = _employee(organization, office, "T2-E")
        hr = create_actor(organization, permissions=("attendance.read", "attendance.manual",
                                                     "employees.read"))[0]
        day = (datetime.now(tz=UTC) - timedelta(days=3)).date()
        response = _client(hr).post(f"{API}/attendance/manual", {
            "employee_id": str(emp.id), "office_id": str(office.id),
            "event_type": "ENTRY", "occurred_at": f"{day.isoformat()}T09:00:00",
            "reason": "забыл отметиться",
        }, format="json")
        assert response.status_code == 201, response.content
        stored = AttendanceEvent.objects.get(id=response.json()["id"]).occurred_at
        expected = datetime(day.year, day.month, day.day, 9, 0, tzinfo=ZoneInfo(office.timezone))
        assert stored == expected, f"ожидалось {expected.astimezone(UTC)}, сохранено {stored}"

    def test_manual_mark_with_explicit_offset_is_respected(self, organization, office):
        from humotech.attendance.models import AttendanceEvent

        emp = _employee(organization, office, "T2b-E")
        hr = create_actor(organization, permissions=("attendance.read", "attendance.manual",
                                                     "employees.read"))[0]
        day = (datetime.now(tz=UTC) - timedelta(days=3)).date()
        response = _client(hr).post(f"{API}/attendance/manual", {
            "employee_id": str(emp.id), "office_id": str(office.id),
            "event_type": "ENTRY", "occurred_at": f"{day.isoformat()}T04:00:00Z",
            "reason": "из интеграции",
        }, format="json")
        assert response.status_code == 201, response.content
        stored = AttendanceEvent.objects.get(id=response.json()["id"]).occurred_at
        assert stored == datetime(day.year, day.month, day.day, 4, 0, tzinfo=UTC)

    def test_morning_mark_for_today_is_not_future(self, organization, office):
        """C-04, побочный симптом: утренняя отметка «за сегодня» (09:00 офиса)
        не отклоняется как будущая, если в офисе уже позже 09:15."""
        emp = _employee(organization, office, "T2c-E")
        hr = create_actor(organization, permissions=("attendance.read", "attendance.manual",
                                                     "employees.read"))[0]
        tz = ZoneInfo(office.timezone)
        now_local = datetime.now(tz=tz)
        if now_local.hour < 10:
            pytest.skip("утро в офисе: 09:00 ещё в будущем — проверка не применима")
        response = _client(hr).post(f"{API}/attendance/manual", {
            "employee_id": str(emp.id), "office_id": str(office.id),
            "event_type": "ENTRY", "occurred_at": f"{now_local.date().isoformat()}T09:00:00",
            "reason": "отметка за сегодня",
        }, format="json")
        assert response.status_code == 201, response.content

    def test_period_summary_days_are_local(self, organization, office):
        """C-26: начало хранится как местная полночь (19:00Z накануне) —
        сводка не должна прибавлять лишний день."""
        from humotech.absences.models import AbsenceRequest, AbsenceType
        from humotech.absences.services import hr_period_summary

        emp = _employee(organization, office, "T3-E")
        tz = ZoneInfo(office.timezone)
        kind = AbsenceType.objects.create(organization=organization, code="UNPAID_T3",
                                          name="За свой счёт", is_paid=False,
                                          requires_approval=True)
        request = AbsenceRequest.objects.create(
            organization=organization, employee=emp, absence_type=kind,
            request_kind="CREATE", status="SUBMITTED",
            requested_start_at=datetime(2026, 10, 20, 0, 0, tzinfo=tz),
            requested_end_at=datetime(2026, 10, 22, 23, 59, 59, tzinfo=tz),
        )
        request.refresh_from_db()  # из базы время приходит в UTC, как в бою
        assert hr_period_summary(request)["calendar_days"] == 3


def test_queue_date_filter_uses_local_days(organization, office):
    """C-26 (очередь): заявка с 20-го по местному времени не попадает в отбор «по 19-е»."""
    from humotech.absences.models import AbsenceRequest, AbsenceType
    from humotech.absences.services import AbsenceService

    organization.default_timezone = office.timezone
    organization.save(update_fields=["default_timezone"])
    emp = _employee(organization, office, "T4-E")
    tz = ZoneInfo(office.timezone)
    kind = AbsenceType.objects.create(organization=organization, code="UNPAID_T4",
                                      name="За свой счёт", is_paid=False,
                                      requires_approval=True)
    request = AbsenceRequest.objects.create(
        organization=organization, employee=emp, absence_type=kind,
        request_kind="CREATE", status="SUBMITTED",
        requested_start_at=datetime(2026, 10, 20, 0, 0, tzinfo=tz),
        requested_end_at=datetime(2026, 10, 22, 23, 59, 59, tzinfo=tz),
    )
    _, hr = create_actor(organization, permissions=("absences.read",))
    service = AbsenceService()
    assert request.id not in set(service.queue(hr, date_to="2026-10-19").values_list("id", flat=True))
    assert request.id in set(service.queue(hr, date_to="2026-10-20").values_list("id", flat=True))
    assert request.id not in set(service.queue(hr, date_from="2026-10-23").values_list("id", flat=True))
    assert request.id in set(service.queue(hr, date_from="2026-10-22").values_list("id", flat=True))


# =============================================================================
# 2. Праздник офиса — только для своего офиса (C-13)
# =============================================================================


def test_office_holiday_applies_only_to_its_office_in_analytics(organization, region):
    from datetime import time

    from humotech.analytics.metrics import AnalyticsService
    from humotech.schedules.models import CalendarException, EmployeeScheduleAssignment
    from humotech.schedules.services import DaySpec, WorkScheduleService

    office_a = make_office(organization, region, "C13-A")
    office_b = make_office(organization, region, "C13-B")
    emp_a = _employee(organization, office_a, "C13-EA")
    emp_b = _employee(organization, office_b, "C13-EB")
    _, wide = create_actor(organization, permissions=("schedules.read", "schedules.manage",
                                                      "analytics.read", "attendance.read",
                                                      "employees.read"))
    days = [DaySpec(weekday=d, is_working_day=d <= 5, start_time=time(9) if d <= 5 else None,
                    end_time=time(18) if d <= 5 else None) for d in range(1, 8)]
    schedule = WorkScheduleService().create(wide, name="5/2", timezone="Asia/Tashkent",
                                            weekly_minutes=2700, days=days)
    for emp in (emp_a, emp_b):
        EmployeeScheduleAssignment.objects.create(organization=organization, employee=emp,
                                                  schedule=schedule, valid_from=date(2026, 1, 1))
    holiday = date(2026, 9, 9)  # среда
    CalendarException.objects.create(organization=organization, office=office_a, date=holiday,
                                     is_working_day=False, name="Праздник офиса A", exception_type="HOLIDAY",
                                     is_active=True)
    report = AnalyticsService().region(wide, region.id, first=holiday, last=holiday)
    # В офисе A день нерабочий, в офисе B — рабочий: ожидается 1 запланированный
    # и 1 пропущенный (сотрудник B не пришёл), а не 0.
    assert report.totals["expected_working_days"] == 1, report.totals
    assert report.totals["missed_days"] == 1, report.totals


# =============================================================================
# 3. Аудитории опросов и фильтры ознакомления — по текущему назначению (A-10/B-03)
# =============================================================================


def _transferred(organization, region):
    office_a = make_office(organization, region, "A10-A")
    office_b = make_office(organization, region, "A10-B")
    yesterday = date.today() - timedelta(days=1)
    emp = _employee(organization, office_a, "A10-E", valid_to=yesterday)
    EmployeeAssignment.objects.create(
        organization=organization, employee=emp, office=office_b,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date.today(),
    )
    return emp, office_a, office_b


def test_survey_audience_by_office_uses_current_assignment(organization, region):
    from humotech.surveys.models import SurveyCampaign
    from humotech.surveys.services import audience_employees

    emp, office_a, office_b = _transferred(organization, region)
    old = SurveyCampaign(organization=organization, audience_kind="OFFICE",
                         audience_ids=[str(office_a.id)])
    new = SurveyCampaign(organization=organization, audience_kind="OFFICE",
                         audience_ids=[str(office_b.id)])
    assert emp not in audience_employees(old), "переведённый получил опрос прежнего офиса"
    assert emp in audience_employees(new)


def test_survey_automation_scope_uses_current_assignment(organization, region):
    from humotech.surveys.automations import _scoped
    from humotech.surveys.models import SurveyAutomation

    emp, office_a, office_b = _transferred(organization, region)
    old = SurveyAutomation(organization=organization, scope={"office_ids": [str(office_a.id)]})
    assert emp.id not in set(_scoped(old).values_list("id", flat=True))
    new = SurveyAutomation(organization=organization, scope={"office_ids": [str(office_b.id)]})
    assert emp.id in set(_scoped(new).values_list("id", flat=True))


def test_onboarding_office_filter_uses_current_assignment(organization, region):
    from humotech.onboarding.models import EmployeeOnboarding
    from humotech.onboarding.services import OnboardingService

    emp, office_a, office_b = _transferred(organization, region)
    from humotech.onboarding.models import OnboardingProgram

    program = OnboardingProgram.objects.create(organization=organization, code="MAIN",
                                               title="Ознакомление")
    EmployeeOnboarding.objects.create(organization=organization, employee=emp, program=program,
                                      status="IN_PROGRESS")
    _, hr = create_actor(organization, permissions=("onboarding.read",))
    service = OnboardingService()
    assert not service._scoped(hr, office_id=office_a.id).filter(employee=emp).exists()
    assert service._scoped(hr, office_id=office_b.id).filter(employee=emp).exists()
