"""Движение сотрудников: принято, уволено, разница.

Проверяется то, что легко посчитать почти правильно:

— приём, посчитанный по дате создания карточки, а не выхода;
— сравнение периода в десять дней с календарным месяцем;
— год назад, посчитанный вычитанием 365 дней;
— 31 марта, ушедшее в несуществующее 31 февраля;
— стажёр, не попавший в численность.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from humotech.analytics.movement import MovementService, _months_back, _years_back
from humotech.employees.models import Employee, EmployeeAssignment

pytestmark = pytest.mark.django_db

API = "/api/v1/analytics/movement"


@pytest.fixture()
def service() -> MovementService:
    return MovementService()


@pytest.fixture()
def hr(make_actor, organization):
    return make_actor(organization, permissions=("employees.read", "offices.read"))


def person(
    organization, office, *, number: str, hired: date,
    left: date | None = None, status: str = "ACTIVE",
):
    employee = Employee.objects.create(
        organization=organization,
        employee_number=number,
        first_name="Тест",
        last_name=number,
        hire_date=hired,
        termination_date=left,
        employment_status="TERMINATED" if left else status,
    )
    EmployeeAssignment.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        employment_type="FULL_TIME",
        work_mode="ONSITE",
        is_primary=True,
        valid_from=hired,
        valid_to=left,
    )
    return employee


# --- подсчёт -----------------------------------------------------------------


class TestCounting:
    def test_hiring_is_counted_by_the_first_working_day(
        self, service, hr, organization, office
    ):
        # Карточку заводят заранее. Считать приём по ней — значит
        # получить всплеск в день, когда кадровик сел за компьютер.
        person(organization, office, number="A", hired=date(2026, 3, 10))

        report = service.report(hr, first=date(2026, 3, 1), last=date(2026, 3, 31))

        assert report.current.hired == 1

    def test_someone_hired_outside_the_period_is_not_counted(
        self, service, hr, organization, office
    ):
        person(organization, office, number="B", hired=date(2026, 2, 28))

        report = service.report(hr, first=date(2026, 3, 1), last=date(2026, 3, 31))

        assert report.current.hired == 0

    def test_dismissals_are_counted_by_their_date(
        self, service, hr, organization, office
    ):
        person(organization, office, number="C", hired=date(2025, 1, 1),
               left=date(2026, 3, 15))

        report = service.report(hr, first=date(2026, 3, 1), last=date(2026, 3, 31))

        assert report.current.left == 1
        assert report.current.difference == -1

    def test_difference_says_which_way_it_went(
        self, service, hr, organization, office
    ):
        person(organization, office, number="D", hired=date(2026, 3, 2))
        person(organization, office, number="E", hired=date(2026, 3, 3))
        person(organization, office, number="F", hired=date(2025, 1, 1),
               left=date(2026, 3, 20))

        report = service.report(hr, first=date(2026, 3, 1), last=date(2026, 3, 31))

        assert (report.current.hired, report.current.left) == (2, 1)
        assert report.current.difference == 1


# --- численность -------------------------------------------------------------


class TestHeadcount:
    def test_trainee_is_part_of_the_headcount(
        self, service, hr, organization, office
    ):
        # Стажёр — работающий человек. Не видеть его в численности значит
        # отчитываться не тем числом, которое стоит в коридоре.
        person(organization, office, number="G", hired=date(2026, 3, 1),
               status="PROBATION")

        report = service.report(hr, first=date(2026, 3, 1), last=date(2026, 3, 31))

        assert report.headcount == 1

    def test_dismissed_person_leaves_the_headcount(
        self, service, hr, organization, office
    ):
        person(organization, office, number="H", hired=date(2025, 1, 1),
               left=date(2026, 3, 10))

        report = service.report(hr, first=date(2026, 3, 1), last=date(2026, 3, 31))

        # На конец периода человека уже нет.
        assert report.headcount == 0

    def test_a_future_hire_is_not_counted_yet(
        self, service, hr, organization, office
    ):
        person(organization, office, number="I", hired=date(2026, 4, 15))

        report = service.report(hr, first=date(2026, 3, 1), last=date(2026, 3, 31))

        assert report.headcount == 0


# --- сравнения ---------------------------------------------------------------


class TestComparisons:
    def test_previous_period_has_the_same_length(self, service, hr):
        report = service.report(hr, first=date(2026, 3, 10), last=date(2026, 3, 19))

        # Десять дней сравниваются с десятью, а не с календарным
        # месяцем: иначе разница объяснялась бы длиной, а не событиями.
        span = (report.previous.last - report.previous.first).days + 1
        assert span == 10
        assert report.previous.last == date(2026, 3, 9)

    def test_month_before_is_the_same_dates_a_month_back(self, service, hr):
        report = service.report(hr, first=date(2026, 3, 1), last=date(2026, 3, 31))

        assert report.month_before.first == date(2026, 2, 1)
        assert report.month_before.last == date(2026, 2, 28)

    def test_year_before_is_shifted_by_a_year_not_by_365_days(self, service, hr):
        # В високосный год сдвиг на 365 дней уезжает на сутки, и март
        # сравнивался бы с концом февраля.
        report = service.report(hr, first=date(2028, 3, 1), last=date(2028, 3, 31))

        assert report.year_before.first == date(2027, 3, 1)
        assert report.year_before.last == date(2027, 3, 31)

    def test_the_thirty_first_does_not_fall_into_a_missing_date(self):
        # 31 марта месяцем раньше — это 28 (или 29) февраля, а не ошибка.
        assert _months_back(date(2026, 3, 31), 1) == date(2026, 2, 28)
        assert _months_back(date(2028, 3, 31), 1) == date(2028, 2, 29)
        assert _months_back(date(2026, 1, 15), 1) == date(2025, 12, 15)

    def test_the_twenty_ninth_of_february_survives_the_shift(self):
        assert _years_back(date(2028, 2, 29), 1) == date(2027, 2, 28)


# --- границы -----------------------------------------------------------------


def test_swapped_dates_are_not_an_empty_answer(service, hr, organization, office):
    person(organization, office, number="J", hired=date(2026, 3, 10))

    report = service.report(hr, first=date(2026, 3, 31), last=date(2026, 3, 1))

    assert report.current.hired == 1


def test_over_http(api_client, make_user, organization, office):
    person(organization, office, number="K", hired=date(2026, 3, 5))
    user = make_user(organization, permissions=("employees.read", "offices.read"))
    api_client.force_authenticate(user=user)

    answer = api_client.get(
        API, {"date_from": "2026-03-01", "date_to": "2026-03-31"}
    )

    assert answer.status_code == 200, answer.content
    body = answer.json()
    assert body["current"]["hired"] == 1
    assert body["current"]["difference"] == 1
    # Март — 31 день, значит предыдущий период тоже 31 день и
    # заканчивается накануне: 29 января — 28 февраля.
    assert body["previous"]["first"] == "2026-01-29"
    assert body["previous"]["last"] == "2026-02-28"
    assert body["headcount"] == 1


def test_without_the_right_it_is_refused(api_client, make_user, organization):
    user = make_user(organization, permissions=("offices.read",))
    api_client.force_authenticate(user=user)

    answer = api_client.get(
        API, {"date_from": "2026-03-01", "date_to": "2026-03-31"}
    )

    assert answer.status_code == 403, answer.content
