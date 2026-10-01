"""Аналитика людей: команда и разрезы «сравнить по».

Проверяется то, из-за чего цифра перестаёт быть правдой:

— уволенный сегодня пропадает из численности прошлого месяца;
— смена должности посчитана переводом;
— командировка лежит в «прочих» отсутствиях;
— разрез по отделам посчитан по другому правилу, чем офисы.
"""

from __future__ import annotations

from datetime import date

import pytest

from django_tests.test_analytics_filters import DAY, came, schedule, utc, worker  # noqa: F401
from humotech.analytics.overview import OverviewService
from humotech.analytics.people import PeopleService
from humotech.core.errors import PermissionDenied
from humotech.departments.models import Department
from humotech.employees.models import EmployeeAssignment

pytestmark = pytest.mark.django_db

API = "/api/v1"
MARCH = (date(2026, 3, 1), date(2026, 3, 31))


@pytest.fixture()
def people() -> PeopleService:
    return PeopleService()


@pytest.fixture()
def analyst(make_actor, organization):
    return make_actor(organization, permissions=("analytics.read", "offices.read"))


def department(organization, code: str, name: str) -> Department:
    return Department.objects.create(
        organization=organization, code=code, name=name, status="ACTIVE"
    )


def hired_on(organization, office, schedule, *, number, day, department=None, status="ACTIVE"):
    person = worker(organization, office, schedule, number=number, department=department)
    person.hire_date = day
    person.employment_status = status
    person.save(update_fields=["hire_date", "employment_status"])
    EmployeeAssignment.objects.filter(employee=person).update(valid_from=day)
    return person


class TestTeam:
    def test_hire_and_departure_move_the_headcount_by_date(
        self, people, analyst, organization, office, schedule
    ):
        worker(organization, office, schedule, number="OLD")
        newcomer = hired_on(organization, office, schedule, number="NEW", day=date(2026, 3, 10))
        leaver = worker(organization, office, schedule, number="GONE")
        leaver.termination_date = date(2026, 3, 20)
        leaver.employment_status = "TERMINATED"
        leaver.save(update_fields=["termination_date", "employment_status"])

        body = people.team(analyst, first=MARCH[0], last=MARCH[1])

        # На 28 февраля — двое: уволенный тогда ещё работал.
        assert body["summary"]["headcount_start"] == 2
        assert body["summary"]["headcount"] == 2
        assert body["summary"]["hired"] == 1
        assert body["summary"]["left"] == 1
        assert [row["name"] for row in body["hires"]] == [f"{newcomer.last_name} {newcomer.first_name}"]
        by_day = {row["day"]: row["headcount"] for row in body["series"]}
        assert by_day["2026-03-09"] == 2
        assert by_day["2026-03-10"] == 3
        assert by_day["2026-03-20"] == 2

    def test_department_change_is_a_transfer_position_change_is_not(
        self, people, analyst, organization, office, schedule
    ):
        sales = department(organization, "SALES", "Продажи")
        support = department(organization, "SUP", "Поддержка")
        moved = worker(organization, office, schedule, number="MOVE", department=sales)
        stayed = worker(organization, office, schedule, number="STAY", department=sales)
        for person, target in ((moved, support), (stayed, sales)):
            EmployeeAssignment.objects.filter(employee=person).update(valid_to=date(2026, 3, 14))
            EmployeeAssignment.objects.create(
                organization=organization, employee=person, office=office,
                department=target, employment_type="FULL_TIME", work_mode="ONSITE",
                is_primary=True, valid_from=date(2026, 3, 15),
            )

        body = people.team(analyst, first=MARCH[0], last=MARCH[1])

        assert [(row["from_department"], row["to_department"]) for row in body["transfers"]] == [
            ("Продажи", "Поддержка"),
        ]
        composition = {row["name"]: row["headcount"] for row in body["departments"]}
        assert composition == {"Продажи": 1, "Поддержка": 1}

    def test_reading_requires_analytics_read(self, people, nobody_actor):
        with pytest.raises(PermissionDenied):
            people.team(nobody_actor, first=MARCH[0], last=MARCH[1])


class TestBreakdowns:
    def test_departments_heads_and_positions_use_the_same_days(
        self, analyst, organization, office, schedule
    ):
        sales = department(organization, "SALES", "Продажи")
        boss = worker(organization, office, schedule, number="BOSS", department=sales)
        sales.head_employee = boss
        sales.save(update_fields=["head_employee"])
        came(organization, boss, office, at=utc(4))
        worker(organization, office, schedule, number="NODEP")

        body = OverviewService().overview(analyst, first=DAY, last=DAY)

        departments = {row["name"]: row["attendance"]["percent"] for row in body["departments"]}
        assert departments == {"Продажи": 100.0, "Не указано": 0.0}
        heads = {row["name"] for row in body["heads"]}
        assert f"{boss.last_name} {boss.first_name}" in heads
        assert body["positions"][0]["name"] == "Не указано"

    def test_business_trip_is_its_own_kind(
        self, analyst, organization, office, schedule, make_absence
    ):
        traveller = worker(organization, office, schedule, number="TRIP")
        make_absence(traveller, code="BUSINESS_TRIP", day=DAY)

        body = OverviewService().overview(analyst, first=DAY, last=DAY)

        assert body["summary"]["trip_days"] == 1
        assert body["summary"]["other_absence_days"] == 0


class TestHttp:
    @pytest.mark.parametrize("path", ["analytics/team"])
    def test_answers_for_the_period(self, api_client, make_user, organization, path):
        api_client.force_authenticate(user=make_user(organization, permissions=("analytics.read",)))
        answer = api_client.get(f"{API}/{path}?date_from=2026-03-01&date_to=2026-03-31")
        assert answer.status_code == 200
        assert "summary" in answer.json()

    def test_without_permission_it_is_403(self, api_client, make_user, organization):
        api_client.force_authenticate(user=make_user(organization, permissions=("employees.read",)))
        assert api_client.get(f"{API}/analytics/team").status_code == 403
