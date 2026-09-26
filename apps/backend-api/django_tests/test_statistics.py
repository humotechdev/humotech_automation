"""Статистика присутствия: границы суток, ночные смены, незакрытые сессии.

Проверяется то, что легко сделать почти правильно. Почти правильная
статистика хуже отсутствующей: по ней принимают решения, а расхождение
в один день обнаруживается через месяц и не воспроизводится.

Ключевые темы здесь три: в каком поясе начинается день, какому дню
принадлежит смена через полночь и что показывать, пока человек не вышел.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest

from humotech.absences.models import (
    AbsenceRequest,
    AbsenceType,
    EmployeeAbsence,
)
from humotech.attendance import statistics
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.attendance.statistics import Presence
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
DUSHANBE = ZoneInfo("Asia/Dushanbe")  # UTC+5, без перехода на летнее время


@pytest.fixture()
def context(db, employee, telegram_settings):
    link_telegram(employee)
    return resolve_by_telegram_user_id(TG_ID)


def utc(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


def mark(context, office, moment, event_type, employee=None):
    """Событие отметки. Сессия без входного события существовать не может.

    Источник MANUAL, а не QR: отметка по QR обязана ссылаться на точку,
    а здесь проверяется счёт времени, а не путь сканирования. Подсовывать
    точку ради обхода ограничения значило бы делать вид, что скан был.
    """
    return AttendanceEvent.objects.create(
        organization=context.organization,
        employee=employee or context.employee,
        office=office,
        event_type=event_type,
        source="MANUAL",
        verification_status="ACCEPTED",
        occurred_at=moment,
        received_at=moment,
    )


def session(context, office, start, end=None, status=None, employee=None):
    """Готовая сессия со своими событиями входа и выхода."""
    duration = int((end - start).total_seconds()) if end else None
    return AttendanceSession.objects.create(
        organization=context.organization,
        employee=employee or context.employee,
        office=office,
        entry_event=mark(context, office, start, "ENTRY", employee),
        exit_event=(
            mark(context, office, end, "EXIT", employee) if end else None
        ),
        started_at=start,
        ended_at=end,
        duration_seconds=duration,
        status=status or ("CLOSED" if end else "OPEN"),
    )


def five_day_schedule(organization, employee, *, valid_from=date(2024, 1, 1),
                      valid_to=None, name="Пятидневка", weekdays=(1, 2, 3, 4, 5)):
    schedule = WorkSchedule.objects.create(
        organization=organization, name=name, timezone="Asia/Dushanbe",
        weekly_minutes=2400, status="ACTIVE",
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
        valid_from=valid_from, valid_to=valid_to,
    )
    return schedule


# --- пояс офиса ------------------------------------------------------------

def test_day_boundary_follows_the_office_not_the_server(context, office):
    """20:00 UTC — это уже следующий день в Душанбе (UTC+5).

    Сервер может стоять где угодно; сотрудник видит свои сутки такими,
    какими они были за окном офиса.
    """
    session(context, office, utc(2026, 9, 2, 20, 0), utc(2026, 9, 2, 22, 0))

    report = statistics.for_period(context, date(2026, 9, 3), date(2026, 9, 3))

    assert report.attended_days == 1, "смена ушла не в те сутки"
    assert report.seconds == 2 * 3600


def test_midnight_in_office_timezone_splits_two_days(context, office):
    # 18:55 UTC = 23:55 в Душанбе — ещё третье
    session(context, office, utc(2026, 9, 3, 18, 55), utc(2026, 9, 3, 18, 58))
    # 19:05 UTC = 00:05 в Душанбе — уже четвёртое
    session(context, office, utc(2026, 9, 3, 19, 5), utc(2026, 9, 3, 19, 8))

    third = statistics.for_period(context, date(2026, 9, 3), date(2026, 9, 3))
    fourth = statistics.for_period(context, date(2026, 9, 4), date(2026, 9, 4))

    assert third.attended_days == 1 and third.completed_sessions == 1
    assert fourth.attended_days == 1 and fourth.completed_sessions == 1


# --- ночная смена ----------------------------------------------------------

def test_night_shift_belongs_to_the_day_it_started(context, office):
    """Смена с 22:00 третьего до 06:00 четвёртого — это смена третьего.

    Для человека это одна смена «в ночь на четвёртое», а не две половинки.
    Резать её пополам значило бы сломать «сколько длилась эта сессия»
    в истории, а этот вопрос задают чаще.
    """
    # 17:00 UTC = 22:00 в Душанбе третьего; 01:00 UTC = 06:00 четвёртого
    session(context, office, utc(2026, 9, 3, 17, 0), utc(2026, 9, 4, 1, 0))

    third = statistics.for_period(context, date(2026, 9, 3), date(2026, 9, 3))
    fourth = statistics.for_period(context, date(2026, 9, 4), date(2026, 9, 4))

    assert third.seconds == 8 * 3600, "ночная смена не попала в день начала"
    assert fourth.seconds == 0
    assert third.days[0].sessions[0].seconds == 8 * 3600


def test_period_total_equals_the_sum_of_days(context, office):
    """Свойство, ради которого выбрано правило «по дню начала».

    Итог периода равен сумме дневных итогов по построению, а не потому,
    что мы за этим следим.
    """
    session(context, office, utc(2026, 9, 1, 4, 0), utc(2026, 9, 1, 12, 0))
    session(context, office, utc(2026, 9, 2, 17, 0), utc(2026, 9, 3, 2, 0))
    session(context, office, utc(2026, 9, 4, 5, 0), utc(2026, 9, 4, 9, 30))

    report = statistics.for_period(context, date(2026, 9, 1), date(2026, 9, 30))

    assert report.seconds == sum(day.seconds for day in report.days)


# --- незакрытая сессия -----------------------------------------------------

def test_open_session_never_gets_an_invented_exit(context, office):
    """Дорисовать конец рабочего дня, которого не было, — значит соврать."""
    started = utc(2026, 9, 3, 4, 0)
    session(context, office, started, None)
    now = started + timedelta(hours=3)

    report = statistics.for_period(
        context, date(2026, 9, 3), date(2026, 9, 3), now=now
    )
    record = report.days[0].sessions[0]

    assert record.is_open
    assert record.ended_at is None
    assert record.seconds == 3 * 3600
    assert record.is_preliminary, "открытая сессия обязана быть помечена"
    assert report.open_sessions == 1
    assert report.completed_sessions == 0


def test_open_session_and_hours_today_are_different_numbers(context, office):
    """В три ночи у зашедшего в 22:00 «сегодня» честно ноль.

    А в офисе он пять часов: сессия принадлежит вчерашнему дню. Свести их
    в одно число значило бы соврать в одном из двух мест.
    """
    # 17:00 UTC = 22:00 третьего в Душанбе
    session(context, office, utc(2026, 9, 3, 17, 0), None)
    # 22:00 UTC = 03:00 четвёртого в Душанбе
    now = utc(2026, 9, 3, 22, 0)

    status = statistics.current_status(context, now=now)

    assert status.day == date(2026, 9, 4)
    assert status.seconds_today == 0, "вчерашняя смена не должна падать в сегодня"
    assert status.open_session is not None
    assert status.open_session.day == date(2026, 9, 3)
    assert status.open_session.seconds == 5 * 3600
    assert status.state == Presence.IN_OFFICE


# --- рабочие дни -----------------------------------------------------------

def test_working_days_come_from_the_schedule(context, office, organization,
                                             employee):
    five_day_schedule(organization, employee)

    # 7-13 сентября 2026: понедельник — воскресенье
    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))

    assert report.working_days == 5
    assert report.has_schedule


def test_without_a_schedule_working_days_are_unknown_not_zero(context):
    """Ноль рабочих дней при нуле пропусков читается как безупречная
    посещаемость. Это совсем другое утверждение."""
    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))

    assert report.has_schedule is False
    assert report.working_days is None
    assert report.missed_days is None


def test_schedule_change_inside_the_period_is_respected(context, office,
                                                        organization, employee):
    """В месяце, внутри которого график поменяли, одна выборка «текущего»
    графика дала бы неверное число рабочих дней на половину периода."""
    five_day_schedule(
        organization, employee, name="Пятидневка",
        valid_from=date(2026, 9, 1), valid_to=date(2026, 9, 15),
    )
    five_day_schedule(
        organization, employee, name="Шестидневка",
        valid_from=date(2026, 9, 16), weekdays=(1, 2, 3, 4, 5, 6),
    )

    first_week = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))
    later_week = statistics.for_period(context, date(2026, 9, 21), date(2026, 9, 27))

    assert first_week.working_days == 5
    assert later_week.working_days == 6


def test_holiday_makes_a_workday_a_day_off(context, office, organization,
                                           employee):
    five_day_schedule(organization, employee)
    CalendarException.objects.create(
        organization=organization, office=None, date=date(2026, 9, 9),
        name="Праздник", exception_type="HOLIDAY", is_working_day=False,
    )

    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))
    assert report.working_days == 4


def test_office_calendar_beats_the_organization_one(context, office,
                                                    organization, employee):
    """Одна дата может иметь и строку офиса, и общую. Побеждает офис."""
    five_day_schedule(organization, employee)
    CalendarException.objects.create(
        organization=organization, office=None, date=date(2026, 9, 9),
        name="Общий праздник", exception_type="HOLIDAY", is_working_day=False,
    )
    CalendarException.objects.create(
        organization=organization, office=office, date=date(2026, 9, 9),
        name="Офис работает", exception_type="WORKING_WEEKEND",
        is_working_day=True,
    )

    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 13))
    assert report.working_days == 5, "правило офиса не перекрыло общее"


def test_missed_day_is_a_working_day_without_attendance_or_absence(
    context, office, organization, employee
):
    five_day_schedule(organization, employee)
    session(context, office, utc(2026, 9, 7, 4, 0), utc(2026, 9, 7, 13, 0))

    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 11))

    assert report.attended_days == 1
    assert report.missed_days == 4


# --- отсутствия ------------------------------------------------------------

def make_absence(context, code, first_day, last_day, *, status="ACTIVE"):
    absence_type = AbsenceType.objects.create(
        organization=context.organization, code=code, name=code.title(),
        requires_approval=True,
    )
    request = AbsenceRequest.objects.create(
        organization=context.organization,
        employee=context.employee,
        absence_type=absence_type,
        request_kind="CREATE",
        status="APPROVED",
        requested_start_at=datetime.combine(
            first_day, datetime.min.time(), tzinfo=DUSHANBE
        ),
        requested_end_at=datetime.combine(
            last_day, datetime.max.time(), tzinfo=DUSHANBE
        ),
        submitted_at=datetime.now(tz=dt_timezone.utc),
    )
    return EmployeeAbsence.objects.create(
        organization=context.organization,
        employee=context.employee,
        absence_type=absence_type,
        origin_request=request,
        start_at=datetime.combine(first_day, datetime.min.time(), tzinfo=DUSHANBE),
        end_at=datetime.combine(last_day, datetime.max.time(), tzinfo=DUSHANBE),
        start_date=first_day,
        end_date=last_day,
        status=status,
    )


def test_sick_leave_days_are_counted_by_local_dates(context):
    """Границы отсутствия — моменты времени, и вычитать их нельзя.

    Разница в днях врёт на сутки при любом переходе через полночь.
    """
    make_absence(context, "SICK_LEAVE", date(2026, 9, 7), date(2026, 9, 9),
                 status="COMPLETED")

    report = statistics.for_period(context, date(2026, 9, 1), date(2026, 9, 30))

    assert report.sick_leave_days == 3
    assert report.vacation_days == 0


def test_vacation_days_are_counted(context):
    make_absence(context, "ANNUAL_LEAVE", date(2026, 9, 14), date(2026, 9, 18),
                 status="PLANNED")

    report = statistics.for_period(context, date(2026, 9, 1), date(2026, 9, 30))
    assert report.vacation_days == 5


def test_unknown_absence_type_does_not_land_in_the_vacation_column(context):
    """Виды отсутствия заводит организация.

    Добавленный ей MATERNITY не должен молча оказаться отпуском — иначе
    цифра отпусков окажется неверной, а понять это будет неоткуда.
    """
    make_absence(context, "MATERNITY", date(2026, 9, 7), date(2026, 9, 8))

    report = statistics.for_period(context, date(2026, 9, 1), date(2026, 9, 30))

    assert report.vacation_days == 0
    assert report.sick_leave_days == 0
    assert report.other_absence_days == 2


def test_cancelled_absence_is_not_counted(context):
    make_absence(context, "SICK_LEAVE", date(2026, 9, 7), date(2026, 9, 9),
                 status="CANCELLED")

    report = statistics.for_period(context, date(2026, 9, 1), date(2026, 9, 30))
    assert report.sick_leave_days == 0


def test_absence_covers_a_working_day_so_it_is_not_missed(
    context, organization, employee
):
    five_day_schedule(organization, employee)
    make_absence(context, "SICK_LEAVE", date(2026, 9, 7), date(2026, 9, 11))

    report = statistics.for_period(context, date(2026, 9, 7), date(2026, 9, 11))

    assert report.missed_days == 0
    assert report.sick_leave_days == 5


# --- текущее состояние -----------------------------------------------------

def test_day_off_is_reported_as_such(context, organization, employee):
    five_day_schedule(organization, employee)
    # 12 сентября 2026 — суббота, 08:00 в Душанбе
    status = statistics.current_status(context, now=utc(2026, 9, 12, 3, 0))

    assert status.state == Presence.DAY_OFF


def test_workday_is_not_declared_missed_before_it_ends(context, organization,
                                                       employee):
    """В десять утра человек ещё может быть в дороге."""
    five_day_schedule(organization, employee)
    # 10:00 в Душанбе в понедельник
    status = statistics.current_status(context, now=utc(2026, 9, 7, 5, 0))

    assert status.state == Presence.OUTSIDE


def test_workday_missed_after_the_schedule_ended(context, organization, employee):
    five_day_schedule(organization, employee)
    # 20:00 в Душанбе, рабочий день кончился в 18:00
    status = statistics.current_status(context, now=utc(2026, 9, 7, 15, 0))

    assert status.state == Presence.WORKDAY_MISSED


def test_being_in_the_office_beats_a_planned_absence(context, office,
                                                     organization, employee):
    """Факт важнее плана: расхождение должно быть видно, а не спрятано."""
    make_absence(context, "ANNUAL_LEAVE", date(2026, 9, 7), date(2026, 9, 11))
    session(context, office, utc(2026, 9, 7, 4, 0), None)

    status = statistics.current_status(context, now=utc(2026, 9, 7, 6, 0))
    assert status.state == Presence.IN_OFFICE


def test_status_reports_the_scheduled_hours(context, organization, employee):
    five_day_schedule(organization, employee)
    status = statistics.current_status(context, now=utc(2026, 9, 7, 5, 0))

    assert status.scheduled_start.isoformat() == "09:00:00"
    assert status.scheduled_end.isoformat() == "18:00:00"


# --- через API -------------------------------------------------------------

def test_bot_and_mini_app_get_the_same_statistics(
    bot_client, api_client, context, office, telegram_settings
):
    """Бот не считает ничего сам — иначе цифры разошлись бы."""
    from .conftest import build_init_data

    session(context, office, utc(2026, 9, 1, 4, 0), utc(2026, 9, 1, 12, 0))

    token = api_client.post(
        "/api/v1/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=TG_ID)},
        format="json",
    ).json()["access_token"]

    params = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    from_bot = bot_client.get("/api/v1/me/statistics", params, **bot_headers(TG_ID))
    from_mini = api_client.get(
        "/api/v1/me/statistics", params, HTTP_AUTHORIZATION=f"Bearer {token}"
    )

    assert from_bot.status_code == from_mini.status_code == 200
    assert from_bot.json() == from_mini.json()
    assert from_bot.json()["summary"]["seconds"] == 8 * 3600


def test_statistics_refuses_a_backwards_period(bot_client, context):
    response = bot_client.get(
        "/api/v1/me/statistics",
        {"date_from": "2026-09-30", "date_to": "2026-09-01"},
        **bot_headers(TG_ID),
    )
    assert response.status_code == 400


def test_statistics_refuses_a_period_longer_than_a_year(bot_client, context):
    """Предел не про удобство: запрос на десять лет строит десять тысяч
    дневных записей на каждый вызов."""
    response = bot_client.get(
        "/api/v1/me/statistics",
        {"date_from": "2016-01-01", "date_to": "2026-01-01"},
        **bot_headers(TG_ID),
    )
    assert response.status_code == 400


def test_history_shows_only_days_worth_showing(bot_client, context, office):
    session(context, office, utc(2026, 9, 1, 4, 0), utc(2026, 9, 1, 12, 0))
    session(context, office, utc(2026, 9, 4, 4, 0), utc(2026, 9, 4, 12, 0))

    body = bot_client.get(
        "/api/v1/me/history",
        {"date_from": "2026-09-01", "date_to": "2026-09-30"},
        **bot_headers(TG_ID),
    ).json()

    assert body["total"] == 2
    # От свежего к старому: историю смотрят, чтобы проверить вчерашнее.
    assert body["days"][0]["day"] == "2026-09-04"
    assert len(body["days"][0]["sessions"]) == 1


def test_history_is_paginated(bot_client, context, office):
    for day in range(1, 11):
        session(context, office, utc(2026, 9, day, 4, 0), utc(2026, 9, day, 12, 0))

    body = bot_client.get(
        "/api/v1/me/history",
        {"date_from": "2026-09-01", "date_to": "2026-09-30", "limit": 3},
        **bot_headers(TG_ID),
    ).json()

    assert len(body["days"]) == 3
    assert body["total"] == 10
    assert body["has_more"] is True


def test_employee_sees_only_their_own_history(bot_client, context, office,
                                              organization, other_organization):
    """Чужие смены не попадают в ответ, и попросить их нечем."""
    from datetime import date as _date

    from humotech.employees.models import Employee

    stranger = Employee.objects.create(
        organization=organization, employee_number="EMP-0777",
        first_name="Чужой", last_name="Сотрудник",
        hire_date=_date(2024, 1, 1), employment_status="ACTIVE",
    )
    session(context, office, utc(2026, 9, 2, 4, 0), utc(2026, 9, 2, 12, 0),
            employee=stranger)

    body = bot_client.get(
        "/api/v1/me/history",
        {"date_from": "2026-09-01", "date_to": "2026-09-30",
         "employee_id": str(stranger.id)},
        **bot_headers(TG_ID),
    ).json()

    assert body["total"] == 0
