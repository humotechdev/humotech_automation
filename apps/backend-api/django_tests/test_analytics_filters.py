"""Фильтры аналитики и новые разрезы: регионы и сотрудники.

Проверяется то, из-за чего фильтр перестаёт быть фильтром:

— выбранный офис, не повлиявший на сравнение и рейтинг;
— отдел, сузивший таблицу, но не KPI;
— регион, посчитанный отдельно от своих офисов и разошедшийся с ними;
— человек без рабочих дней, объявленный худшим по явке;
— рейтинг на тысячу строк, который никто не читает.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone

import pytest

from django_tests.conftest import make_qr_point
from humotech.analytics.overview import PEOPLE_LIMIT, OverviewService
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.departments.models import Department
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)

pytestmark = pytest.mark.django_db

DAY = date(2026, 3, 10)  # вторник


def utc(hour: int, minute: int = 0, *, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=dt_timezone.utc)


@pytest.fixture()
def service() -> OverviewService:
    return OverviewService()


@pytest.fixture()
def analyst(make_actor, organization):
    return make_actor(
        organization, permissions=("analytics.read", "offices.read")
    )


@pytest.fixture()
def schedule(organization):
    plan = WorkSchedule.objects.create(
        organization=organization, name="Пятидневка", timezone="Asia/Dushanbe",
        weekly_minutes=40 * 60, status="ACTIVE", late_grace_minutes=15,
    )
    for weekday in range(1, 8):
        ScheduleDay.objects.create(
            schedule=plan, weekday=weekday, is_working_day=weekday <= 5,
            start_time=time(9, 0) if weekday <= 5 else None,
            end_time=time(18, 0) if weekday <= 5 else None,
        )
    return plan


def worker(organization, office, schedule, *, number: str, department=None):
    employee = Employee.objects.create(
        organization=organization, employee_number=number,
        first_name="Тест", last_name=number, hire_date=date(2025, 1, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=employee, office=office,
        department=department, employment_type="FULL_TIME", work_mode="ONSITE",
        is_primary=True, valid_from=date(2025, 1, 1),
    )
    EmployeeScheduleAssignment.objects.create(
        organization=organization, employee=employee, schedule=schedule,
        valid_from=date(2025, 1, 1),
    )
    return employee


def came(organization, employee, office, *, at):
    """Закрытый день: вход и выход.

    Оба события настоящие — закрытая смена без события выхода
    отвергается ограничением базы, и правильно отвергается: у неё нет
    основания.
    """
    point = make_qr_point(
        organization, office, code=f"P-{employee.employee_number}"
    )
    entry = AttendanceEvent.objects.create(
        organization=organization, employee=employee, office=office,
        qr_point=point, event_type="ENTRY", source="QR",
        verification_status="ACCEPTED", occurred_at=at,
    )
    left = at + timedelta(hours=8)
    exit_event = AttendanceEvent.objects.create(
        organization=organization, employee=employee, office=office,
        qr_point=point, event_type="EXIT", source="QR",
        verification_status="ACCEPTED", occurred_at=left,
    )
    return AttendanceSession.objects.create(
        organization=organization, employee=employee, office=office,
        entry_event=entry, exit_event=exit_event, started_at=at,
        ended_at=left, duration_seconds=8 * 3600, status="CLOSED",
    )


# --- сужение состава ---------------------------------------------------------


class TestNarrowing:
    def test_department_narrows_the_numbers_not_just_the_table(
        self, service, analyst, organization, office, schedule
    ):
        sales = Department.objects.create(
            organization=organization, code="SALES", name="Продажи", status="ACTIVE"
        )
        one = worker(organization, office, schedule, number="A", department=sales)
        worker(organization, office, schedule, number="B")
        came(organization, one, office, at=utc(4))  # 09:00 по Душанбе

        whole = service.overview(analyst, first=DAY, last=DAY)
        narrow = service.overview(analyst, first=DAY, last=DAY, department_id=sales.id)

        # Иначе таблица показывает отдел, а KPI — всю компанию, и
        # человек сравнивает несравнимое.
        assert whole["summary"]["attendance"]["denominator"] == 2
        assert narrow["summary"]["attendance"]["denominator"] == 1
        assert narrow["summary"]["attendance"]["percent"] == 100

    def test_employee_filter_leaves_only_that_person(
        self, service, analyst, organization, office, schedule
    ):
        one = worker(organization, office, schedule, number="C")
        worker(organization, office, schedule, number="D")

        narrow = service.overview(analyst, first=DAY, last=DAY, employee_id=one.id)

        assert narrow["summary"]["attendance"]["denominator"] == 1
        assert [row["name"] for row in narrow["employees"]] == ["C Тест"]


# --- регионы -----------------------------------------------------------------


class TestRegions:
    def test_region_is_the_sum_of_its_offices(
        self, service, analyst, organization, office, schedule
    ):
        one = worker(organization, office, schedule, number="E")
        worker(organization, office, schedule, number="F")
        came(organization, one, office, at=utc(4))

        body = service.overview(analyst, first=DAY, last=DAY)

        assert len(body["regions"]) == 1
        region = body["regions"][0]
        # Складывать офисы дважды по-разному — способ однажды получить
        # два разных числа про одно и то же.
        assert region["attendance"]["numerator"] == 1
        assert region["attendance"]["denominator"] == 2
        assert region["position"] == 1

    def test_office_filter_reaches_the_regions(
        self, service, analyst, organization, office, other_office, schedule
    ):
        worker(organization, office, schedule, number="G")
        worker(organization, other_office, schedule, number="H")

        narrow = service.overview(analyst, first=DAY, last=DAY, office_id=office.id)

        # Выбрали офис — сравнение относится к нему, а не ко всей
        # компании: иначе фильтр обманывает.
        assert len(narrow["regions"]) == 1
        assert narrow["regions"][0]["attendance"]["denominator"] == 1


# --- рейтинг людей -----------------------------------------------------------


class TestPeople:
    def test_worst_attendance_comes_first(
        self, service, analyst, organization, office, schedule
    ):
        good = worker(organization, office, schedule, number="I")
        worker(organization, office, schedule, number="J")  # не пришёл
        came(organization, good, office, at=utc(4))

        body = service.overview(analyst, first=DAY, last=DAY)

        # Страницу открывают, чтобы найти проблему, а не полюбоваться
        # отличниками.
        assert [row["name"] for row in body["employees"]] == ["J Тест", "I Тест"]
        assert body["employees"][0]["missed_days"] == 1

    def test_lateness_is_counted_beyond_the_grace(
        self, service, analyst, organization, office, schedule
    ):
        late = worker(organization, office, schedule, number="K")
        # 09:40 по Душанбе при начале в 09:00 и допуске 15 минут.
        came(organization, late, office, at=utc(4, 40))

        body = service.overview(analyst, first=DAY, last=DAY)

        row = next(one for one in body["employees"] if one["name"] == "K Тест")
        assert row["late_days"] == 1
        # Двадцать пять, а не сорок: организация, разрешившая приходить
        # на четверть часа позже, считает опозданием именно их.
        assert row["late_minutes"] == 25

    def test_person_without_working_days_is_not_the_worst(
        self, service, analyst, organization, office, schedule
    ):
        saturday = date(2026, 3, 14)
        worker(organization, office, schedule, number="L")

        body = service.overview(analyst, first=saturday, last=saturday)

        # Его просто не с чем сравнить: выходной — не прогул.
        assert body["employees"] == []

    def test_the_rating_has_a_ceiling(self):
        # Рейтинг на тысячу строк никто не читает, а весит он столько
        # же, сколько вся остальная страница.
        assert PEOPLE_LIMIT <= 100


# --- через HTTP --------------------------------------------------------------


def test_filters_over_http(api_client, make_user, organization, office, schedule):
    worker(organization, office, schedule, number="M")
    user = make_user(organization, permissions=("analytics.read", "offices.read"))
    api_client.force_authenticate(user=user)

    answer = api_client.get("/api/v1/analytics/overview", {
        "date_from": DAY.isoformat(), "date_to": DAY.isoformat(),
        "office_id": str(office.id),
    })

    assert answer.status_code == 200, answer.content
    body = answer.json()
    assert "regions" in body and "employees" in body
