"""Обзор аналитики: те же правила, что у отчёта, плюс разрезы для страницы.

Главное, что проверяется: выходные и будущие дни — не неявка; опоздание
считается от личной смены с допуском; незакрытое посещение не попадает
в среднее время; разница с прошлым периодом — в процентных пунктах;
рейтинг идёт от лучшей явки к худшей.
"""

from __future__ import annotations

from datetime import date, datetime, time
from datetime import timezone as dt_timezone

import pytest

from django_tests.test_hr_analytics import five_day_schedule, worked
from django_tests.test_hr_attendance import give_schedule, open_session
from django_tests.test_hr_employee_card import make_person
from humotech.analytics.overview import EARLIEST, OverviewService
from humotech.core.errors import PermissionDenied
from humotech.core.timeframes import office_zone

pytestmark = pytest.mark.django_db

API = "/api/v1"

# Понедельник 2 марта — воскресенье 8 марта 2026: пять рабочих дней и выходные.
MONDAY = date(2026, 3, 2)
SUNDAY = date(2026, 3, 8)
# «Сейчас» — после периода: ни один его день не будущий.
LATER = datetime(2026, 3, 31, 12, 0, tzinfo=dt_timezone.utc)


@pytest.fixture()
def service() -> OverviewService:
    return OverviewService()


@pytest.fixture()
def analyst(make_actor, organization):
    return make_actor(organization, permissions=("analytics.read",))


def day_of(body: dict, day: date) -> dict:
    return next(one for one in body["days"] if one["day"] == day.isoformat())


def test_weekend_is_not_a_missed_day(service, analyst, organization, employee, office):
    five_day_schedule(organization, employee)
    for offset in range(4):  # понедельник — четверг, пятница пропущена
        worked(organization, employee, office, date(2026, 3, 2 + offset))

    body = service.overview(analyst, first=MONDAY, last=SUNDAY, now=LATER)

    assert body["summary"]["attendance"] == {"numerator": 4, "denominator": 5, "percent": 80.0}
    assert body["summary"]["missed_days"] == 1
    saturday = day_of(body, date(2026, 3, 7))
    assert saturday["working"] is False
    assert saturday["percent"] is None
    assert day_of(body, date(2026, 3, 6))["missed"] == 1


def test_late_is_counted_after_the_grace(service, analyst, organization, employee, office):
    five_day_schedule(organization, employee, grace=10)
    worked(organization, employee, office, date(2026, 3, 2), came=time(8, 50))   # до начала
    worked(organization, employee, office, date(2026, 3, 3), came=time(9, 5))    # в допуске
    worked(organization, employee, office, date(2026, 3, 4), came=time(9, 25))   # опоздание

    body = service.overview(analyst, first=MONDAY, last=date(2026, 3, 4), now=LATER)

    assert body["summary"]["on_time"]["numerator"] == 2
    assert body["summary"]["late"] == {"numerator": 1, "denominator": 3, "percent": 33.3}
    arrivals = body["arrivals"]
    assert arrivals["after_start"] == 2  # 09:05 и 09:25 — после начала смены
    assert arrivals["start_time"] == "09:00"
    columns = {column["from"]: column for column in arrivals["buckets"]}
    assert columns[-10]["early"] == 1
    assert columns[0]["grace"] == 1
    assert columns[20]["late"] == 1


def test_arrival_is_measured_from_the_personal_shift(
    service, analyst, organization, office
):
    """Смена в 10:00 и приход в 10:05 — пять минут, а не час опоздания."""
    person, _ = make_person(organization, office, number="EMP-LATE-SHIFT")
    give_schedule(organization, person, start=time(10, 0))
    worked(organization, person, office, MONDAY, came=time(10, 5))

    body = service.overview(analyst, first=MONDAY, last=MONDAY, now=LATER)

    columns = {column["from"]: column for column in body["arrivals"]["buckets"]}
    assert columns[0]["late"] == 1
    assert sum(column["late"] for column in body["arrivals"]["buckets"]) == 1
    assert body["arrivals"]["from_minutes"] == EARLIEST


def test_open_visit_is_not_in_the_average_time(
    service, analyst, organization, employee, office
):
    five_day_schedule(organization, employee)
    worked(organization, employee, office, MONDAY, came=time(9, 0), left=time(17, 0))
    tz = office_zone(office)
    started = datetime.combine(date(2026, 3, 3), time(9, 0), tzinfo=tz).astimezone(dt_timezone.utc)
    open_session(organization, employee, office, started=started)

    body = service.overview(analyst, first=MONDAY, last=date(2026, 3, 3), now=LATER)

    assert body["summary"]["attendance"]["numerator"] == 2
    assert body["summary"]["average_seconds"] == 8 * 3600
    assert body["summary"]["open_sessions"] == 1


def test_future_days_are_not_missed(service, analyst, organization, employee, office):
    five_day_schedule(organization, employee)
    worked(organization, employee, office, MONDAY)
    tuesday_noon = datetime.combine(date(2026, 3, 3), time(7, 0), tzinfo=dt_timezone.utc)

    body = service.overview(analyst, first=MONDAY, last=SUNDAY, now=tuesday_noon)

    # Во вторник он ещё не пришёл: вторник — сегодня и считается; среда и
    # дальше — будущее и в знаменатель не входят.
    assert body["summary"]["attendance"]["denominator"] == 2
    assert day_of(body, date(2026, 3, 4))["future"] is True
    assert day_of(body, date(2026, 3, 4))["expected"] == 0


def test_previous_period_difference_is_in_points(
    service, analyst, organization, employee, office
):
    five_day_schedule(organization, employee)
    # Прошлая неделя: 2 из 5. Эта: 4 из 5.
    for day in (23, 24):
        worked(organization, employee, office, date(2026, 2, day))
    for day in (2, 3, 4, 5):
        worked(organization, employee, office, date(2026, 3, day))

    body = service.overview(analyst, first=MONDAY, last=SUNDAY, now=LATER)

    assert body["previous_period"] == {"first": "2026-02-23", "last": "2026-03-01"}
    assert body["summary"]["previous_attendance"]["percent"] == 40.0
    assert body["summary"]["difference_points"] == 40.0


def test_weekday_detail_narrows_every_block(
    service, analyst, organization, employee, office
):
    five_day_schedule(organization, employee)
    worked(organization, employee, office, MONDAY)  # только понедельник

    body = service.overview(analyst, first=MONDAY, last=SUNDAY, weekday=1, now=LATER)

    assert body["summary"]["attendance"] == {"numerator": 1, "denominator": 1, "percent": 100.0}
    assert day_of(body, date(2026, 3, 3))["in_detail"] is False
    monday = next(one for one in body["weekdays"]["days"] if one["weekday"] == 1)
    assert monday["attendance"]["percent"] == 100.0


def test_offices_are_ranked_from_best_to_worst(
    service, analyst, organization, employee, office, other_office
):
    five_day_schedule(organization, employee)
    person, _ = make_person(organization, other_office, number="EMP-BRANCH")
    five_day_schedule(organization, person)
    for day in (2, 3, 4, 5, 6):
        worked(organization, person, other_office, date(2026, 3, day))
    worked(organization, employee, office, MONDAY)

    body = service.overview(analyst, first=MONDAY, last=SUNDAY, now=LATER)

    ranking = body["offices"]
    assert [row["name"] for row in ranking[:2]] == [other_office.name, office.name]
    assert ranking[0]["position"] == 1
    assert ranking[0]["attendance"]["percent"] == 100.0
    assert ranking[1]["attendance"]["percent"] == 20.0


def test_reading_requires_analytics_read(service, nobody_actor):
    with pytest.raises(PermissionDenied):
        service.overview(nobody_actor, first=MONDAY, last=SUNDAY)


class TestHttp:
    def test_overview_comes_back_with_every_block(
        self, api_client, make_user, organization, employee, office
    ):
        five_day_schedule(organization, employee)
        worked(organization, employee, office, MONDAY)
        api_client.force_authenticate(
            user=make_user(organization, permissions=("analytics.read",))
        )

        answer = api_client.get(
            f"{API}/analytics/overview?date_from={MONDAY}&date_to={SUNDAY}&office_id={office.id}"
        )

        assert answer.status_code == 200
        body = answer.json()
        assert {"summary", "days", "previous_days", "offices", "arrivals", "weekdays"} <= set(body)
        assert len(body["days"]) == 7

    def test_bad_weekday_is_a_validation_error(self, api_client, make_user, organization):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("analytics.read",))
        )
        answer = api_client.get(f"{API}/analytics/overview?weekday=monday")
        assert answer.status_code == 400

    def test_without_permission_it_is_403(self, api_client, make_user, organization):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("employees.read",))
        )
        answer = api_client.get(f"{API}/analytics/overview?date_from={MONDAY}&date_to={SUNDAY}")
        assert answer.status_code == 403
