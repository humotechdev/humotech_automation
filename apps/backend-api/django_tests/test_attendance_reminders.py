"""Напоминание о начале дня и ответы «Опаздываю» / «Не приду».

Проверяется то, из-за чего такие рассылки перестают работать:

— сообщение, ушедшее человеку в отпуске или на больничном;
— второе, третье и десятое напоминание за один день;
— вопрос «вы будете в офисе?», заданный в восемь вечера;
— «не приду», молча превратившееся в оформленный отпуск;
— напоминание уволенному и тому, у кого нет графика;
— два ответа за день, из которых табель выбирает случайный.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from django_tests.conftest import bot_headers, link_telegram, make_qr_point
from humotech.attendance import reminders
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceEvent, DayNotice
from humotech.core.errors import ValidationFailed
from humotech.notifications.models import Notification
from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)

pytestmark = pytest.mark.django_db

DAY = date(2026, 3, 10)  # вторник


#: Пояс офиса из фикстуры. Время смены задаётся в нём, а не в UTC:
#: «рабочий день начался в девять» — это девять у человека, а не у
#: сервера, и тест, написанный в UTC, проверял бы другой час.
OFFICE_ZONE = ZoneInfo("Asia/Dushanbe")


def utc(hour: int, minute: int = 0, *, day: date = DAY) -> datetime:
    """Момент местного времени офиса."""
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=OFFICE_ZONE)


@pytest.fixture()
def schedule(organization, employee):
    """Пятидневка с началом в 09:00 по поясу офиса и допуском 15 минут."""
    plan = WorkSchedule.objects.create(
        organization=organization, name="Пятидневка",
        timezone="Asia/Dushanbe", weekly_minutes=40 * 60,
        status="ACTIVE", late_grace_minutes=15,
    )
    for weekday in range(1, 8):
        ScheduleDay.objects.create(
            schedule=plan,
            weekday=weekday,
            is_working_day=weekday <= 5,
            start_time=time(9, 0) if weekday <= 5 else None,
            end_time=time(18, 0) if weekday <= 5 else None,
        )
    EmployeeScheduleAssignment.objects.create(
        organization=organization, employee=employee, schedule=plan,
        valid_from=date(2024, 1, 1),
    )
    return plan


@pytest.fixture()
def reachable(employee):
    """Привязанный Telegram: без него писать некуда."""
    return link_telegram(employee, telegram_user_id=555_000_111)


def mark(organization, employee, office, *, at):
    return AttendanceEvent.objects.create(
        organization=organization, employee=employee, office=office,
        qr_point=make_qr_point(organization, office, code="REM-1"),
        event_type="ENTRY", source="QR", verification_status="ACCEPTED",
        occurred_at=at,
    )


# --- кому и когда ------------------------------------------------------------


class TestWhen:
    def test_nobody_is_reminded_before_the_grace_is_over(
        self, schedule, reachable, employee
    ):
        # 09:10 — допуск ещё идёт. Человек, вошедший в дверь, получил бы
        # вопрос «вы сегодня будете в офисе?» стоя у турникета.
        assert reminders.due(utc(9, 10)) == []

    def test_reminder_is_due_right_after_the_grace(
        self, schedule, reachable, employee
    ):
        found = reminders.due(utc(9, 16))

        assert [one.employee.id for one in found] == [employee.id]
        assert found[0].start_time == time(9, 0)

    def test_evening_is_too_late_to_ask(self, schedule, reachable, employee):
        # Без верхней границы забывший отметиться утром получил бы вопрос
        # в восемь вечера — когда день уже разбирают в CRM, а не в чате.
        assert reminders.due(utc(20, 0)) == []

    def test_day_off_by_schedule_is_left_alone(
        self, schedule, reachable, employee
    ):
        saturday = date(2026, 3, 14)
        assert reminders.due(utc(9, 30, day=saturday)) == []


# --- кого не трогают ---------------------------------------------------------


class TestWho:
    def test_marked_person_is_not_reminded(
        self, schedule, reachable, organization, employee, office
    ):
        mark(organization, employee, office, at=utc(8, 55))

        assert reminders.due(utc(9, 30)) == []

    def test_person_on_leave_is_not_reminded(
        self, schedule, reachable, employee, make_absence
    ):
        make_absence(employee, code="ANNUAL_LEAVE", day=DAY)

        # Написать человеку в отпуске «вы сегодня будете в офисе?» — это
        # не напоминание, а повод перестать доверять боту.
        assert reminders.due(utc(9, 30)) == []

    def test_dismissed_person_is_not_reminded(
        self, schedule, reachable, employee
    ):
        employee.employment_status = "TERMINATED"
        employee.save(update_fields=["employment_status"])

        assert reminders.due(utc(9, 30)) == []

    def test_person_without_telegram_is_skipped(self, schedule, employee):
        # Сообщение копилось бы в очереди впустую: доставить его некуда.
        assert reminders.due(utc(9, 30)) == []

    def test_person_who_already_answered_is_not_asked_again(
        self, schedule, reachable, organization, employee
    ):
        reminders.notice(employee=employee, kind="LATE", now=utc(9, 20))

        assert reminders.due(utc(9, 40)) == []


# --- отправка ----------------------------------------------------------------


class TestSending:
    def test_one_message_per_day_no_matter_how_often_we_check(
        self, schedule, reachable, employee
    ):
        first = reminders.run_once(utc(9, 16))
        again = reminders.run_once(utc(9, 21))
        third = reminders.run_once(utc(10, 0))

        assert first == 1
        # Человек, которому бот написал трижды, отключает бота — и тогда
        # он не получит ни напоминания, ни уведомления о заявке.
        assert (again, third) == (0, 0)
        assert Notification.objects.filter(employee_id=employee.id).count() == 1

    def test_message_names_the_start_of_the_shift(
        self, schedule, reachable, employee
    ):
        reminders.run_once(utc(9, 16))

        body = Notification.objects.get(employee_id=employee.id).body
        assert "09:00" in body
        # Вопрос, а не упрёк: человек мог застрять в пробке или забыть
        # приложить телефон.
        assert body.endswith("Вы сегодня будете в офисе?")


# --- ответ человека ----------------------------------------------------------


class TestNotice:
    def test_late_is_recorded_with_the_reason(self, employee):
        row = reminders.notice(
            employee=employee, kind="LATE", comment="Пробки", now=utc(9, 20)
        )

        assert (row.kind, row.comment) == ("LATE", "Пробки")
        assert row.day == DAY

    def test_reason_is_optional(self, employee):
        row = reminders.notice(employee=employee, kind="LATE", now=utc(9, 20))

        # Требовать объяснение у того, кто стоит в пробке, — способ не
        # получить ни объяснения, ни предупреждения.
        assert row.comment is None

    def test_second_answer_replaces_the_first(self, employee):
        reminders.notice(employee=employee, kind="LATE", comment="Пробки",
                         now=utc(9, 20))
        reminders.notice(employee=employee, kind="ABSENT", comment="Заболел",
                         now=utc(10, 0))

        rows = DayNotice.objects.filter(employee_id=employee.id, day=DAY)
        # Иначе табель выбирал бы из двух записей случайную.
        assert rows.count() == 1
        assert rows.first().kind == "ABSENT"

    def test_absence_is_not_created_by_saying_i_will_not_come(self, employee):
        from humotech.absences.models import EmployeeAbsence

        reminders.notice(employee=employee, kind="ABSENT", now=utc(9, 30))

        # Иначе отсутствие оформлялось бы одной кнопкой мимо HR.
        assert not EmployeeAbsence.objects.filter(employee_id=employee.id).exists()

    def test_unknown_kind_is_refused(self, employee):
        with pytest.raises(ValidationFailed):
            reminders.notice(employee=employee, kind="MAYBE")


# --- как это видит кадровик --------------------------------------------------


class TestPresence:
    @pytest.fixture()
    def hr_actor(self, make_actor, organization):
        return make_actor(
            organization,
            permissions=("attendance.read", "employees.read", "offices.read"),
        )

    def test_warned_person_is_late_not_missing(
        self, schedule, hr_actor, employee
    ):
        reminders.notice(employee=employee, kind="LATE", comment="Пробки",
                         now=utc(9, 20))

        row = AttendanceHrService().presence(hr_actor, day=DAY).rows[0]

        # Предупредивший о задержке — не прогул, и называть его так
        # значит наказывать за предупреждение.
        assert row.state == "LATE"
        assert (row.notice_kind, row.notice_comment) == ("LATE", "Пробки")

    def test_silent_person_is_still_missing(self, schedule, hr_actor, employee):
        row = AttendanceHrService().presence(hr_actor, day=DAY).rows[0]

        assert row.state == "NOT_COME"
        assert row.notice_kind is None

    def test_mark_beats_the_warning(
        self, schedule, hr_actor, organization, employee, office
    ):
        from humotech.attendance.models import AttendanceSession

        reminders.notice(employee=employee, kind="LATE", now=utc(9, 20))
        entry = mark(organization, employee, office, at=utc(9, 40))
        AttendanceSession.objects.create(
            organization=organization, employee=employee, office=office,
            entry_event=entry, started_at=entry.occurred_at, status="OPEN",
        )

        row = AttendanceHrService().presence(hr_actor, day=DAY).rows[0]

        # Факт сильнее сказанного: человек уже пришёл.
        assert row.state in {"IN_OFFICE", "LEFT"}


# --- через HTTP --------------------------------------------------------------


def test_notice_over_http(employee, reachable, bot_client, telegram_settings):
    answer = bot_client.post(
        "/api/v1/me/attendance/notice",
        {"kind": "LATE", "comment": "Пробки"},
        format="json",
        **bot_headers(reachable.telegram_user_id),
    )

    assert answer.status_code == 200, answer.content
    assert answer.json()["kind"] == "LATE"
    assert DayNotice.objects.filter(employee_id=employee.id).count() == 1
