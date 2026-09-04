"""Снятие и возврат календарного исключения.

Продолжение `test_hr_calendar.py`: там заведение, пересечения и влияние
на расчёт, здесь — снятие с действия.

Снятие не то же самое, что удаление, и различие не косметическое.
Удаление говорит «такой строки не должно было быть» и применяется
к опечаткам. Снятие говорит «так было, потом отменили» — и через
полгода именно оно объясняет расхождение в табеле.

Главный тест здесь тот же по духу, что и в соседнем файле: снятие,
которое ничего не меняет в расчёте, — галочка в интерфейсе, а не
операция. Поэтому проверяется настоящим присутствием, а не полем в базе.

Второе — освобождение даты. Уникальные ключи переписаны на условие
«действующее», иначе снятая строка продолжала бы занимать день: отменили
перенос — и завести на него ничего нельзя, а отказ приходит из
ограничения целостности.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from humotech.attendance.hr import AttendanceHrService
from humotech.audit.models import AuditLog
from humotech.core.errors import Conflict, PermissionDenied
from humotech.schedules.calendar import CalendarExceptionService
from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)

pytestmark = pytest.mark.django_db

API = "/api/v1"

# Суббота: по обычному графику выходной, поэтому «рабочий выходной»
# на неё виден в расчёте сразу и без оговорок.
SATURDAY = date(2026, 3, 28)
MONDAY = date(2026, 3, 23)


@pytest.fixture()
def service() -> CalendarExceptionService:
    return CalendarExceptionService()


@pytest.fixture()
def calendar_actor(make_actor, organization):
    return make_actor(
        organization, permissions=("schedules.read", "calendar.manage")
    )


@pytest.fixture()
def reader_actor(make_actor, organization):
    return make_actor(organization, permissions=("schedules.read",))


@pytest.fixture()
def calendar_client(api_client, make_user, organization, office):
    api_client.force_authenticate(
        user=make_user(
            organization,
            permissions=("schedules.read", "calendar.manage", "offices.read"),
        )
    )
    return api_client


@pytest.fixture()
def hr_with_attendance(make_actor, organization, employee, office):
    """Кадровик, который видит и календарь, и присутствие."""
    _give_schedule(organization, employee, office)
    return make_actor(
        organization,
        permissions=("attendance.read", "schedules.read", "calendar.manage"),
    )


def _give_schedule(organization, employee, office):
    schedule = WorkSchedule.objects.create(
        organization=organization,
        name="Пятидневка",
        timezone="Asia/Dushanbe",
        weekly_minutes=2400,
        late_grace_minutes=0,
        status="ACTIVE",
    )
    for weekday in range(1, 6):  # понедельник-пятница
        ScheduleDay.objects.create(
            schedule=schedule, weekday=weekday, is_working_day=True,
            start_time=time(9, 0), end_time=time(18, 0),
        )
    for weekday in (6, 7):
        ScheduleDay.objects.create(
            schedule=schedule, weekday=weekday, is_working_day=False,
        )
    EmployeeScheduleAssignment.objects.create(
        organization=organization, employee=employee,
        schedule=schedule, valid_from=date(2024, 1, 1),
    )
    return schedule


def working_weekend(service, actor, office):
    return service.create(
        actor, name="Перенос за 23 марта",
        exception_type="WORKING_WEEKEND", date_from=SATURDAY,
        office_id=office.id,
    )[0]


def holiday(service, actor, office):
    return service.create(
        actor, name="Навруз", exception_type="HOLIDAY",
        date_from=MONDAY, office_id=office.id,
    )[0]


# --- влияние на расчёт -------------------------------------------------------


class TestAffectsCalculation:
    def test_deactivation_gives_the_day_off_back(
        self, service, hr_with_attendance, office
    ):
        """Снятие, ничего не меняющее в расчёте, — галочка, а не операция.

        Рабочая суббота сделала выходной рабочим днём; снятие обязано
        вернуть выходной, и проверяется это настоящим присутствием.
        """
        attendance = AttendanceHrService()
        created = working_weekend(service, hr_with_attendance, office)
        assert attendance.presence(
            hr_with_attendance, day=SATURDAY, office_id=office.id
        ).rows[0].state != "DAY_OFF"

        service.deactivate(hr_with_attendance, created.id)

        assert attendance.presence(
            hr_with_attendance, day=SATURDAY, office_id=office.id
        ).rows[0].state == "DAY_OFF"

    def test_reactivation_makes_the_day_working_again(
        self, service, hr_with_attendance, office
    ):
        attendance = AttendanceHrService()
        created = working_weekend(service, hr_with_attendance, office)
        service.deactivate(hr_with_attendance, created.id)

        service.reactivate(hr_with_attendance, created.id)

        assert attendance.presence(
            hr_with_attendance, day=SATURDAY, office_id=office.id
        ).rows[0].state != "DAY_OFF"


# --- строка и список ---------------------------------------------------------


class TestTheRowSurvives:
    def test_the_row_stays_with_its_data(self, service, calendar_actor, office):
        created = holiday(service, calendar_actor, office)

        service.deactivate(calendar_actor, created.id)

        created.refresh_from_db()
        assert created.is_active is False
        assert created.name == "Навруз"

    def test_the_default_list_shows_only_active(
        self, service, calendar_actor, office
    ):
        """Умолчание — действующий календарь.

        Снятые нужны, когда разбираются в прошлом, а не когда смотрят,
        какие дни в этом месяце рабочие.
        """
        created = holiday(service, calendar_actor, office)
        service.deactivate(calendar_actor, created.id)

        visible = service.list(calendar_actor, office_id=office.id)
        history = service.list(
            calendar_actor, office_id=office.id, include_inactive=True
        )

        assert [row.id for row in visible.items] == []
        assert [row.id for row in history.items] == [created.id]


# --- дата освобождается ------------------------------------------------------


class TestTheDateIsFreed:
    def test_a_new_exception_fits_the_freed_date(
        self, service, calendar_actor, office
    ):
        """Ради этого и переписаны оба уникальных ключа.

        Снятая строка, продолжающая занимать дату, сделала бы снятие
        бессмысленным: отменили перенос — и завести на этот день ничего
        нельзя.
        """
        first = working_weekend(service, calendar_actor, office)
        service.deactivate(calendar_actor, first.id)

        second = service.create(
            calendar_actor, name="Другой праздник", exception_type="HOLIDAY",
            date_from=SATURDAY, office_id=office.id,
        )

        assert len(second) == 1
        assert second[0].id != first.id

    def test_returning_into_an_occupied_date_is_a_clear_refusal(
        self, service, calendar_actor, office
    ):
        """Понятный отказ, а не сообщение про индекс.

        Без этой проверки ответ пришёл бы из ограничения целостности —
        текстом про уникальный ключ вместо текста про календарь.
        """
        first = working_weekend(service, calendar_actor, office)
        service.deactivate(calendar_actor, first.id)
        service.create(
            calendar_actor, name="Другой праздник", exception_type="HOLIDAY",
            date_from=SATURDAY, office_id=office.id,
        )

        with pytest.raises(Conflict):
            service.reactivate(calendar_actor, first.id)

    def test_two_active_exceptions_on_one_day_are_still_impossible(
        self, service, calendar_actor, office
    ):
        """Переписанные ключи не ослабили запрет, а сузили его до
        действующих строк."""
        working_weekend(service, calendar_actor, office)

        with pytest.raises(Conflict):
            service.create(
                calendar_actor, name="Второе утверждение",
                exception_type="HOLIDAY", date_from=SATURDAY,
                office_id=office.id,
            )


# --- права и журнал ----------------------------------------------------------


class TestRulesAndJournal:
    def test_deactivating_twice_is_refused(
        self, service, calendar_actor, office
    ):
        created = holiday(service, calendar_actor, office)
        service.deactivate(calendar_actor, created.id)

        with pytest.raises(Conflict):
            service.deactivate(calendar_actor, created.id)

    def test_reactivating_an_active_one_is_refused(
        self, service, calendar_actor, office
    ):
        created = holiday(service, calendar_actor, office)

        with pytest.raises(Conflict):
            service.reactivate(calendar_actor, created.id)

    def test_both_actions_need_calendar_manage(
        self, service, calendar_actor, reader_actor, office
    ):
        created = holiday(service, calendar_actor, office)

        with pytest.raises(PermissionDenied):
            service.deactivate(reader_actor, created.id)

    def test_a_foreign_organization_cannot_reach_it(
        self, service, calendar_actor, make_actor, other_organization, office
    ):
        """Чужая организация отвечает как отсутствие записи.

        Права у соседа ровно те же — иначе тест упёрся бы в отказ по
        правам и ничего не сказал бы про изоляцию.
        """
        from humotech.core.errors import NotFound

        outsider = make_actor(
            other_organization,
            permissions=("schedules.read", "calendar.manage"),
        )
        created = holiday(service, calendar_actor, office)

        with pytest.raises(NotFound):
            service.deactivate(outsider, created.id)

    def test_both_actions_are_recorded(self, service, calendar_actor, office):
        created = holiday(service, calendar_actor, office)
        service.deactivate(calendar_actor, created.id)
        service.reactivate(calendar_actor, created.id)

        actions = list(
            AuditLog.objects.filter(
                entity_type="calendar_exceptions", entity_id=created.id
            ).order_by("occurred_at").values_list("action", flat=True)
        )
        assert actions == [
            "calendar_exception.create",
            "calendar_exception.deactivate",
            "calendar_exception.reactivate",
        ]

    def test_the_journal_shows_the_change_on_both_sides(
        self, service, calendar_actor, office
    ):
        created = holiday(service, calendar_actor, office)
        service.deactivate(calendar_actor, created.id)

        entry = AuditLog.objects.get(
            entity_type="calendar_exceptions", entity_id=created.id,
            action="calendar_exception.deactivate",
        )
        assert entry.old_values["is_active"] is True
        assert entry.new_values["is_active"] is False


# --- HTTP --------------------------------------------------------------------


class TestHttp:
    def test_both_endpoints_answer(self, calendar_client, office):
        created = calendar_client.post(
            f"{API}/calendar-exceptions/",
            {"name": "Навруз", "exception_type": "HOLIDAY",
             "date_from": MONDAY.isoformat(), "office_id": str(office.id)},
            format="json",
        ).json()["items"][0]

        off = calendar_client.post(
            f"{API}/calendar-exceptions/{created['id']}/deactivate/"
        )
        assert off.status_code == 200
        assert off.json()["is_active"] is False

        on = calendar_client.post(
            f"{API}/calendar-exceptions/{created['id']}/reactivate/"
        )
        assert on.status_code == 200
        assert on.json()["is_active"] is True

    def test_the_list_hides_deactivated_by_default(
        self, calendar_client, office
    ):
        created = calendar_client.post(
            f"{API}/calendar-exceptions/",
            {"name": "Навруз", "exception_type": "HOLIDAY",
             "date_from": MONDAY.isoformat(), "office_id": str(office.id)},
            format="json",
        ).json()["items"][0]
        calendar_client.post(
            f"{API}/calendar-exceptions/{created['id']}/deactivate/"
        )

        visible = calendar_client.get(f"{API}/calendar-exceptions/").json()
        history = calendar_client.get(
            f"{API}/calendar-exceptions/", {"include_inactive": "true"}
        ).json()

        assert visible["items"] == []
        assert [row["id"] for row in history["items"]] == [created["id"]]

    def test_a_reader_cannot_deactivate(
        self, api_client, make_user, organization, office, service,
        calendar_actor,
    ):
        created = holiday(service, calendar_actor, office)
        api_client.force_authenticate(
            user=make_user(organization, permissions=("schedules.read",))
        )

        response = api_client.post(
            f"{API}/calendar-exceptions/{created.id}/deactivate/"
        )

        assert response.status_code == 403
