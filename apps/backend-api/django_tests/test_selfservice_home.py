"""Что главный экран Mini App спрашивает у сервера.

Две темы. Первая — норма рабочего дня: без неё клиенту нечем отличить
«отработал день» от «отработал два часа», и кольцо недели пришлось бы
красить по восьми часам, взятым с потолка. Вторая — лента уведомлений:
в приложении показывается только дошедшее, а не всё, что завели.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import timezone as dt_timezone

import pytest

from humotech.attendance import statistics
from humotech.notifications.models import Notification
from humotech.schedules.models import (
    CalendarException,
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)
from humotech.telegram.identity import resolve_by_telegram_user_id

from .conftest import bot_headers, link_telegram

pytestmark = pytest.mark.django_db

TG_ID = 777_000_111


@pytest.fixture()
def context(db, employee, telegram_settings):
    link_telegram(employee)
    return resolve_by_telegram_user_id(TG_ID)


def schedule_for(
    organization,
    employee,
    *,
    weekly_minutes: int = 2400,
    weekdays: tuple[int, ...] = (1, 2, 3, 4, 5),
    valid_from: date = date(2020, 1, 1),
):
    schedule = WorkSchedule.objects.create(
        organization=organization, name="График", timezone="Asia/Dushanbe",
        weekly_minutes=weekly_minutes, status="ACTIVE",
    )
    for weekday in range(1, 8):
        working = weekday in weekdays
        ScheduleDay.objects.create(
            schedule=schedule, weekday=weekday, is_working_day=working,
            start_time="09:00" if working else None,
            end_time="18:00" if working else None,
        )
    EmployeeScheduleAssignment.objects.create(
        organization=organization, employee=employee, schedule=schedule,
        valid_from=valid_from,
    )
    return schedule


# --- норма дня -------------------------------------------------------------

def test_daily_norm_is_the_weekly_contract_split_over_working_days(
    context, organization, employee
):
    """Сорок часов на пять дней — восемь, а не девять.

    Девять получилось бы из «18:00 минус 09:00»: в эту разницу входит
    обед, которого график не описывает ни минутой.
    """
    schedule_for(organization, employee)

    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))
    monday = report.days[0]

    assert monday.day == date(2026, 9, 7)
    assert monday.norm_seconds == 8 * 3600


def test_day_off_has_a_norm_of_zero_not_unknown(context, organization, employee):
    """У выходного норма известна и равна нулю: график про него знает."""
    schedule_for(organization, employee)

    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))
    saturday = report.days[5]

    assert saturday.is_working_day is False
    assert saturday.norm_seconds == 0


def test_without_a_schedule_the_norm_is_unknown_not_zero(context):
    """Ноль означал бы «работать не нужно». Это другое утверждение."""
    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))

    assert all(day.norm_seconds is None for day in report.days)


def test_six_day_schedule_gets_a_shorter_day(context, organization, employee):
    """Те же сорок часов на шесть дней — по шесть часов сорок минут."""
    schedule_for(organization, employee, weekdays=(1, 2, 3, 4, 5, 6))

    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))

    assert report.days[0].norm_seconds == (2400 // 6) * 60


def test_holiday_keeps_the_norm_at_zero(context, organization, employee, office):
    """Праздник делает рабочий день выходным — вместе с его нормой."""
    schedule_for(organization, employee)
    CalendarException.objects.create(
        organization=organization, office=office, date=date(2026, 9, 9),
        name="Праздник", exception_type="HOLIDAY", is_working_day=False,
    )

    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))
    wednesday = report.days[2]

    assert wednesday.is_working_day is False
    assert wednesday.norm_seconds == 0


def test_statistics_endpoint_carries_the_norm(
    context, organization, employee, bot_client
):
    schedule_for(organization, employee)

    response = bot_client.get(
        "/api/v1/me/statistics",
        {"date_from": "2026-09-07", "date_to": "2026-09-13"},
        **bot_headers(),
    )

    assert response.status_code == 200
    days = response.json()["days"]
    assert days[0]["norm_seconds"] == 8 * 3600
    assert days[5]["norm_seconds"] == 0


# --- уведомления -----------------------------------------------------------

def made(employee, *, status: str = "SENT", title: str = "Объявление",
         read_at=None, notification_type: str = "absence.approved"):
    return Notification.objects.create(
        organization=employee.organization,
        employee=employee,
        channel="IN_APP",
        notification_type=notification_type,
        title=title,
        body="Текст уведомления",
        status=status,
        sent_at=datetime(2026, 9, 10, 8, 0, tzinfo=dt_timezone.utc),
        read_at=read_at,
        # RUNNING без времени захвата запрещён проверкой в базе: строка,
        # которую «взял отправщик», обязана помнить, когда её взяли.
        locked_at=(
            datetime(2026, 9, 10, 7, 0, tzinfo=dt_timezone.utc)
            if status == "RUNNING" else None
        ),
    )


def test_feed_shows_delivered_and_counts_unread(context, employee, bot_client):
    made(employee, title="Первое")
    made(employee, title="Второе",
         read_at=datetime(2026, 9, 10, 9, 0, tzinfo=dt_timezone.utc))

    response = bot_client.get("/api/v1/me/notifications", **bot_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["unread"] == 1
    assert {item["title"] for item in body["items"]} == {"Первое", "Второе"}


@pytest.mark.parametrize("status", ["PENDING", "RUNNING", "FAILED", "CANCELLED"])
def test_undelivered_notifications_stay_out_of_the_feed(
    context, employee, bot_client, status
):
    """Строка в очереди ещё не сообщение: она может не уйти вовсе."""
    made(employee, status=status)

    response = bot_client.get("/api/v1/me/notifications", **bot_headers())

    assert response.json() == {"unread": 0, "items": []}


def test_someone_elses_notification_is_not_visible(
    context, organization, employee, office, bot_client
):
    from humotech.employees.models import Employee

    stranger = Employee.objects.create(
        organization=organization, employee_number="E-9001",
        first_name="Чужой", last_name="Сотрудник",
        hire_date=date(2020, 1, 1), employment_status="ACTIVE",
    )
    made(stranger, title="Не для вас")

    response = bot_client.get("/api/v1/me/notifications", **bot_headers())

    assert response.json()["items"] == []


def test_marking_read_clears_the_counter(context, employee, bot_client):
    row = made(employee)

    response = bot_client.post(
        f"/api/v1/me/notifications/{row.id}/read", **bot_headers()
    )

    assert response.status_code == 200
    assert response.json()["is_read"] is True
    row.refresh_from_db()
    assert row.status == "READ"
    feed = bot_client.get("/api/v1/me/notifications", **bot_headers())
    assert feed.json()["unread"] == 0


def test_marking_read_twice_is_not_an_error(context, employee, bot_client):
    """Второе нажатие не должно двигать время прочтения."""
    row = made(employee)

    first = bot_client.post(
        f"/api/v1/me/notifications/{row.id}/read", **bot_headers()
    )
    second = bot_client.post(
        f"/api/v1/me/notifications/{row.id}/read", **bot_headers()
    )

    assert second.status_code == 200
    assert second.json()["read_at"] == first.json()["read_at"]


def test_cannot_mark_someone_elses_notification(
    context, organization, employee, bot_client
):
    from humotech.employees.models import Employee

    stranger = Employee.objects.create(
        organization=organization, employee_number="E-9002",
        first_name="Чужой", last_name="Сотрудник",
        hire_date=date(2020, 1, 1), employment_status="ACTIVE",
    )
    row = made(stranger)

    response = bot_client.post(
        f"/api/v1/me/notifications/{row.id}/read", **bot_headers()
    )

    assert response.status_code == 404
    row.refresh_from_db()
    assert row.read_at is None
