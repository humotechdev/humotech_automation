"""Аналитика людей: команда, стажировки и разрезы «сравнить по».

Проверяется то, из-за чего цифра перестаёт быть правдой:

— уволенный сегодня пропадает из численности прошлого месяца;
— перевод в штат выдуман по косвенным признакам, а не взят из журнала;
— смена должности посчитана переводом;
— стажёр, у которого решение через три дня, стоит в конце списка;
— командировка лежит в «прочих» отсутствиях;
— разрез по отделам посчитан по другому правилу, чем офисы.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.utils import timezone as django_timezone

from django_tests.test_analytics_filters import DAY, came, schedule, utc, worker  # noqa: F401
from humotech.analytics.overview import OverviewService
from humotech.analytics.people import PeopleService
from humotech.audit.models import AuditLog
from humotech.core.errors import PermissionDenied
from humotech.departments.models import Department
from humotech.employees.lifecycle import PROBATION_FAILED
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

    def test_promotion_comes_from_the_journal(
        self, people, analyst, organization, office, schedule
    ):
        trainee = hired_on(organization, office, schedule, number="TR", day=date(2026, 1, 10))
        AuditLog.objects.create(
            organization=organization, action="employee.promote",
            entity_type="employees", entity_id=trainee.id,
            occurred_at=datetime(2026, 3, 12, 8, 0, tzinfo=dt_timezone.utc),
        )

        body = people.team(analyst, first=MARCH[0], last=MARCH[1])

        assert body["summary"]["promoted"] == 1
        assert body["summary"]["previous_promoted"] == 0

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


class TestProbation:
    def test_nearest_decision_goes_first_and_is_due(
        self, people, analyst, organization, office, schedule
    ):
        today = django_timezone.localdate()
        late = hired_on(organization, office, schedule, number="LATE", day=today - timedelta(days=20), status="PROBATION")
        soon = hired_on(organization, office, schedule, number="SOON", day=today - timedelta(days=60), status="PROBATION")
        late.probation_to = today + timedelta(days=40)
        soon.probation_to = today + timedelta(days=3)
        for one in (late, soon):
            one.probation_from = one.hire_date
            one.save(update_fields=["probation_to", "probation_from"])

        body = people.probation(analyst, first=today - timedelta(days=29), last=today)

        assert [row["employee_number"] for row in body["trainees"]] == ["SOON", "LATE"]
        assert body["trainees"][0]["days_left"] == 3
        assert body["summary"]["active"] == 2
        assert body["summary"]["due"] == 1
        assert body["summary"]["started"] == 1  # LATE вышел внутри периода

    def test_start_without_its_own_date_is_the_hire_date(
        self, people, analyst, organization, office, schedule
    ):
        """Стажёр без даты начала стажировки начал её в день выхода."""
        today = django_timezone.localdate()
        hired_on(organization, office, schedule, number="NODATE", day=today - timedelta(days=5), status="PROBATION")
        hired_on(organization, office, schedule, number="STAFF", day=today - timedelta(days=5))

        body = people.probation(analyst, first=today - timedelta(days=29), last=today)

        assert body["summary"]["started"] == 1
        assert body["trainees"][0]["days_left"] is None

    def test_failed_probation_is_the_termination_reason(
        self, people, analyst, organization, office, schedule
    ):
        failed = worker(organization, office, schedule, number="FAIL")
        failed.termination_date = date(2026, 3, 5)
        failed.termination_reason = PROBATION_FAILED
        failed.employment_status = "TERMINATED"
        failed.save(update_fields=["termination_date", "termination_reason", "employment_status"])

        body = people.probation(analyst, first=MARCH[0], last=MARCH[1])

        assert body["summary"]["failed"] == 1
        assert body["summary"]["conversion_percent"] == 0


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
    @pytest.mark.parametrize("path", ["analytics/team", "analytics/probation"])
    def test_answers_for_the_period(self, api_client, make_user, organization, path):
        api_client.force_authenticate(user=make_user(organization, permissions=("analytics.read",)))
        answer = api_client.get(f"{API}/{path}?date_from=2026-03-01&date_to=2026-03-31")
        assert answer.status_code == 200
        assert "summary" in answer.json()

    def test_without_permission_it_is_403(self, api_client, make_user, organization):
        api_client.force_authenticate(user=make_user(organization, permissions=("employees.read",)))
        assert api_client.get(f"{API}/analytics/team").status_code == 403


class TestMentor:
    def test_mentor_is_set_in_the_card_and_shown_in_probation(
        self, people, analyst, api_client, make_user, organization, office, schedule
    ):
        today = django_timezone.localdate()
        trainee = hired_on(organization, office, schedule, number="TRAINEE", day=today - timedelta(days=10), status="PROBATION")
        mentor = worker(organization, office, schedule, number="MENTOR")
        api_client.force_authenticate(user=make_user(organization, permissions=("employees.read", "employees.manage")))

        made = api_client.patch(f"{API}/employees/{trainee.id}/", {"mentor_employee_id": str(mentor.id)}, format="json")
        refused = api_client.patch(f"{API}/employees/{trainee.id}/", {"mentor_employee_id": str(trainee.id)}, format="json")

        assert made.status_code == 200, made.json()
        assert made.json()["mentor"] == {"id": str(mentor.id), "full_name": f"{mentor.last_name} {mentor.first_name}"}
        assert refused.status_code == 400
        row = people.probation(analyst, first=today - timedelta(days=29), last=today)["trainees"][0]
        assert row["mentor"]["id"] == str(mentor.id)
