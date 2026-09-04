"""Календарные исключения: область, запрет пересечений, влияние на расчёт.

Главный тест здесь — не CRUD, а `TestAffectsCalculation`. Календарь,
который редактируется, но ни на что не влияет, — это форма без функции:
ровно та ошибка, которую легко не заметить, потому что все остальные
тесты при ней зелёные.

Второе по важности — запрет неоднозначных пересечений. Он держится на
двух уникальных ограничениях в базе, а не на проверке в коде, и тест
проверяет именно результат: второе утверждение про тот же день не
заводится, чем бы оно ни было.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from humotech.attendance.hr import AttendanceHrService
from humotech.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from humotech.schedules.calendar import CalendarExceptionService
from humotech.schedules.models import (
    CalendarException,
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)

API = "/api/v1"

# Суббота: по обычному графику это выходной, поэтому «рабочий выходной»
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


# --- права и область ---------------------------------------------------------


@pytest.mark.django_db
class TestAccess:
    def test_reading_requires_permission(self, service, nobody_actor):
        with pytest.raises(PermissionDenied):
            service.list(nobody_actor)

    def test_writing_requires_calendar_manage(self, service, reader_actor):
        with pytest.raises(PermissionDenied):
            service.create(
                reader_actor,
                name="Навруз",
                exception_type="HOLIDAY",
                date_from=SATURDAY,
            )

    def test_empty_scope_shows_only_organization_wide(
        self, service, make_actor, organization, other_region, office
    ):
        """Регион без офисов — права есть, территории нет.

        Здесь легче всего написать `if visible:` и показать весь
        календарь компании. Общие по организации исключения при этом
        видны намеренно: они не про конкретный офис и не раскрывают,
        какие офисы вообще существуют.
        """
        CalendarException.objects.create(
            organization=organization, office=office, date=SATURDAY,
            name="Только для офиса", exception_type="WORKING_WEEKEND",
            is_working_day=True,
        )
        CalendarException.objects.create(
            organization=organization, office=None, date=MONDAY,
            name="Общий праздник", exception_type="HOLIDAY",
            is_working_day=False,
        )
        stranger = make_actor(
            organization, permissions=("schedules.read",), region=other_region
        )

        names = [row.name for row in service.list(stranger).items]
        assert names == ["Общий праздник"]

    def test_office_admin_cannot_declare_a_company_wide_holiday(
        self, service, make_actor, organization, office
    ):
        local = make_actor(
            organization,
            permissions=("schedules.read", "calendar.manage"),
            office=office,
        )
        with pytest.raises(ValidationFailed) as exc:
            service.create(
                local, name="Выходной", exception_type="HOLIDAY",
                date_from=SATURDAY,
            )
        assert "вся организация" in str(exc.value)

    def test_office_admin_cannot_delete_a_company_wide_holiday(
        self, service, make_actor, organization, office
    ):
        row = CalendarException.objects.create(
            organization=organization, office=None, date=MONDAY,
            name="Навруз", exception_type="HOLIDAY", is_working_day=False,
        )
        local = make_actor(
            organization,
            permissions=("schedules.read", "calendar.manage"),
            office=office,
        )
        # Видеть — да, отменять для всей компании — нет.
        with pytest.raises(NotFound):
            service.delete(local, row.id)

    def test_foreign_organization_is_invisible(
        self, service, calendar_actor, other_organization, foreign_office
    ):
        row = CalendarException.objects.create(
            organization=other_organization, office=foreign_office,
            date=MONDAY, name="Чужой праздник", exception_type="HOLIDAY",
            is_working_day=False,
        )
        with pytest.raises(NotFound):
            service.get(calendar_actor, row.id)


# --- создание ----------------------------------------------------------------


@pytest.mark.django_db
class TestCreate:
    def test_period_becomes_one_row_per_day(
        self, service, calendar_actor, office
    ):
        created = service.create(
            calendar_actor,
            name="Навруз",
            exception_type="HOLIDAY",
            date_from=date(2026, 3, 21),
            date_to=date(2026, 3, 24),
            office_id=office.id,
        )
        assert [row.date for row in created] == [
            date(2026, 3, 21), date(2026, 3, 22),
            date(2026, 3, 23), date(2026, 3, 24),
        ]
        assert all(row.is_working_day is False for row in created)

    def test_region_expands_into_its_offices(
        self, service, calendar_actor, organization, region, office
    ):
        from django_tests.conftest import make_office

        second = make_office(organization, region, "OFFICE-2")
        created = service.create(
            calendar_actor,
            name="Перенос",
            exception_type="WORKING_WEEKEND",
            date_from=SATURDAY,
            region_id=region.id,
        )
        assert {row.office_id for row in created} == {office.id, second.id}
        # Собственной строки у региона нет: колонка region_id добавила бы
        # третье измерение уникальности, которое противоречило бы офисному.
        assert all(row.office_id is not None for row in created)

    def test_working_flag_follows_the_type(self, service, calendar_actor, office):
        holiday = service.create(
            calendar_actor, name="Праздник", exception_type="HOLIDAY",
            date_from=MONDAY, office_id=office.id,
        )[0]
        working = service.create(
            calendar_actor, name="Перенос", exception_type="WORKING_WEEKEND",
            date_from=SATURDAY, office_id=office.id,
        )[0]
        assert holiday.is_working_day is False
        assert working.is_working_day is True

    def test_second_exception_on_the_same_day_is_refused(
        self, service, calendar_actor, office
    ):
        """Запрет неоднозначных пересечений держит база, а не код."""
        service.create(
            calendar_actor, name="Праздник", exception_type="HOLIDAY",
            date_from=MONDAY, office_id=office.id,
        )
        with pytest.raises(Conflict):
            service.create(
                calendar_actor, name="Рабочий день", exception_type="WORKING_WEEKEND",
                date_from=MONDAY, office_id=office.id,
            )

    def test_overlapping_period_is_refused_whole(
        self, service, calendar_actor, office
    ):
        """Пересечение отменяет весь период, а не часть его.

        Половина заведённых дней хуже, чем ни одного: кадровик увидел бы
        успех и не заметил, что три дня из пяти не легли.
        """
        service.create(
            calendar_actor, name="Праздник", exception_type="HOLIDAY",
            date_from=date(2026, 3, 23), office_id=office.id,
        )
        with pytest.raises(Conflict):
            service.create(
                calendar_actor, name="Отпускная неделя", exception_type="HOLIDAY",
                date_from=date(2026, 3, 21), date_to=date(2026, 3, 25),
                office_id=office.id,
            )
        assert CalendarException.objects.filter(office=office).count() == 1

    def test_reversed_period_is_a_clear_error(
        self, service, calendar_actor, office
    ):
        with pytest.raises(ValidationFailed):
            service.create(
                calendar_actor, name="Ошибка", exception_type="HOLIDAY",
                date_from=date(2026, 3, 25), date_to=date(2026, 3, 21),
                office_id=office.id,
            )

    def test_absurd_range_is_refused(self, service, calendar_actor, office):
        with pytest.raises(ValidationFailed) as exc:
            service.create(
                calendar_actor, name="Опечатка в годе",
                exception_type="HOLIDAY",
                date_from=date(2026, 1, 1), date_to=date(2030, 1, 1),
                office_id=office.id,
            )
        assert "366" in str(exc.value)

    def test_unknown_type_is_refused(self, service, calendar_actor, office):
        with pytest.raises(ValidationFailed):
            service.create(
                calendar_actor, name="?", exception_type="SNOW_DAY",
                date_from=MONDAY, office_id=office.id,
            )


# --- изменение и снятие ------------------------------------------------------


@pytest.mark.django_db
class TestUpdateAndDelete:
    def test_changing_type_changes_the_working_flag(
        self, service, calendar_actor, office
    ):
        row = service.create(
            calendar_actor, name="Праздник", exception_type="HOLIDAY",
            date_from=MONDAY, office_id=office.id,
        )[0]
        updated = service.update(
            calendar_actor, row.id, exception_type="WORKING_WEEKEND"
        )
        assert updated.is_working_day is True

    def test_delete_removes_the_row(self, service, calendar_actor, office):
        row = service.create(
            calendar_actor, name="Праздник", exception_type="HOLIDAY",
            date_from=MONDAY, office_id=office.id,
        )[0]
        service.delete(calendar_actor, row.id)
        assert not CalendarException.objects.filter(id=row.id).exists()

    def test_every_change_is_recorded(self, service, calendar_actor, office):
        from humotech.audit.models import AuditLog

        row = service.create(
            calendar_actor, name="Праздник", exception_type="HOLIDAY",
            date_from=MONDAY, office_id=office.id,
        )[0]
        service.update(calendar_actor, row.id, name="Навруз")
        service.delete(calendar_actor, row.id)

        actions = list(
            AuditLog.objects.filter(entity_id=row.id)
            .order_by("occurred_at")
            .values_list("action", flat=True)
        )
        assert actions == [
            "calendar_exception.create",
            "calendar_exception.update",
            "calendar_exception.delete",
        ]

    def test_deleted_row_leaves_its_snapshot_behind(
        self, service, calendar_actor, office
    ):
        from humotech.audit.models import AuditLog

        row = service.create(
            calendar_actor, name="Праздник", exception_type="HOLIDAY",
            date_from=MONDAY, office_id=office.id,
        )[0]
        service.delete(calendar_actor, row.id)

        record = AuditLog.objects.get(action="calendar_exception.delete")
        assert record.old_values["name"] == "Праздник"
        assert record.new_values is None


# --- влияние на расчёт -------------------------------------------------------


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
        organization=organization,
        employee=employee,
        schedule=schedule,
        valid_from=date(2024, 1, 1),
    )
    return schedule


@pytest.mark.django_db
class TestAffectsCalculation:
    """Календарь, который ни на что не влияет, — форма без функции.

    Расчёт читает исключения на каждом запросе, ничего не кешируя, и эти
    тесты проверяют именно это: строка появилась — ответ изменился.
    """

    def test_holiday_turns_a_working_day_into_a_day_off(
        self, organization, employee, office
    ):
        from django_tests.conftest import create_actor

        _give_schedule(organization, employee, office)
        _, hr = create_actor(
            organization,
            permissions=("attendance.read", "schedules.read", "calendar.manage"),
        )
        attendance = AttendanceHrService()

        before = attendance.presence(hr, day=MONDAY, office_id=office.id)
        assert before.rows[0].state == "NOT_COME"

        CalendarExceptionService().create(
            hr, name="Навруз", exception_type="HOLIDAY",
            date_from=MONDAY, office_id=office.id,
        )

        after = attendance.presence(hr, day=MONDAY, office_id=office.id)
        assert after.rows[0].state == "DAY_OFF"

    def test_working_weekend_turns_a_day_off_into_a_working_day(
        self, organization, employee, office
    ):
        from django_tests.conftest import create_actor

        _give_schedule(organization, employee, office)
        _, hr = create_actor(
            organization,
            permissions=("attendance.read", "schedules.read", "calendar.manage"),
        )
        attendance = AttendanceHrService()

        before = attendance.presence(hr, day=SATURDAY, office_id=office.id)
        assert before.rows[0].state == "DAY_OFF"

        CalendarExceptionService().create(
            hr, name="Перенос за 23 марта", exception_type="WORKING_WEEKEND",
            date_from=SATURDAY, office_id=office.id,
            reason="приказ №14 от 03.03",
        )

        after = attendance.presence(hr, day=SATURDAY, office_id=office.id)
        assert after.rows[0].state == "NOT_COME"

    def test_office_exception_wins_over_the_organization_one(
        self, organization, employee, office
    ):
        """Приоритет уже определён в расчёте — здесь он проверяется через API.

        Общий по компании праздник и рабочий день в конкретном офисе —
        это не противоречие, а обычная ситуация: филиал работает, когда
        головной офис отдыхает.
        """
        from django_tests.conftest import create_actor

        _give_schedule(organization, employee, office)
        _, hr = create_actor(
            organization,
            permissions=("attendance.read", "schedules.read", "calendar.manage"),
        )
        calendar = CalendarExceptionService()

        calendar.create(
            hr, name="Общий выходной", exception_type="HOLIDAY",
            date_from=MONDAY,
        )
        calendar.create(
            hr, name="Филиал работает", exception_type="WORKING_WEEKEND",
            date_from=MONDAY, office_id=office.id,
        )

        report = AttendanceHrService().presence(
            hr, day=MONDAY, office_id=office.id
        )
        assert report.rows[0].state == "NOT_COME"

    def test_removing_the_exception_restores_the_calculation(
        self, organization, employee, office
    ):
        from django_tests.conftest import create_actor

        _give_schedule(organization, employee, office)
        _, hr = create_actor(
            organization,
            permissions=("attendance.read", "schedules.read", "calendar.manage"),
        )
        calendar = CalendarExceptionService()
        row = calendar.create(
            hr, name="Навруз", exception_type="HOLIDAY",
            date_from=MONDAY, office_id=office.id,
        )[0]
        assert (
            AttendanceHrService()
            .presence(hr, day=MONDAY, office_id=office.id)
            .rows[0]
            .state
            == "DAY_OFF"
        )

        calendar.delete(hr, row.id)

        # Отменённый праздник обязан исчезнуть из расчёта, иначе он не отменён.
        assert (
            AttendanceHrService()
            .presence(hr, day=MONDAY, office_id=office.id)
            .rows[0]
            .state
            == "NOT_COME"
        )


# --- HTTP --------------------------------------------------------------------


@pytest.fixture()
def calendar_client(api_client, make_user, organization, office):
    user = make_user(
        organization,
        permissions=("schedules.read", "calendar.manage", "offices.read"),
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.mark.django_db
class TestHttp:
    def test_create_returns_every_created_day(self, calendar_client, office):
        response = calendar_client.post(
            f"{API}/calendar-exceptions/",
            {
                "name": "Навруз",
                "exception_type": "HOLIDAY",
                "date_from": "2026-03-21",
                "date_to": "2026-03-23",
                "office_id": str(office.id),
            },
            format="json",
        )
        assert response.status_code == 201
        items = response.json()["items"]
        assert len(items) == 3
        assert [row["date"] for row in items] == [
            "2026-03-21", "2026-03-22", "2026-03-23",
        ]

    def test_list_is_ordered_by_date_not_by_creation(
        self, calendar_client, office
    ):
        """Курсор здесь по дате: календарь листают по дням."""
        for day in ("2026-03-25", "2026-03-21", "2026-03-23"):
            calendar_client.post(
                f"{API}/calendar-exceptions/",
                {"name": f"День {day}", "exception_type": "HOLIDAY",
                 "date_from": day, "office_id": str(office.id)},
                format="json",
            )
        response = calendar_client.get(
            f"{API}/calendar-exceptions/", {"office_id": str(office.id)}
        )
        assert response.status_code == 200
        dates = [row["date"] for row in response.json()["items"]]
        assert dates == ["2026-03-21", "2026-03-23", "2026-03-25"]

    def test_conflict_is_409_not_500(self, calendar_client, office):
        payload = {
            "name": "Праздник", "exception_type": "HOLIDAY",
            "date_from": "2026-03-23", "office_id": str(office.id),
        }
        assert calendar_client.post(
            f"{API}/calendar-exceptions/", payload, format="json"
        ).status_code == 201
        second = calendar_client.post(
            f"{API}/calendar-exceptions/", payload, format="json"
        )
        assert second.status_code == 409

    def test_delete_returns_204(self, calendar_client, office):
        created = calendar_client.post(
            f"{API}/calendar-exceptions/",
            {"name": "Праздник", "exception_type": "HOLIDAY",
             "date_from": "2026-03-23", "office_id": str(office.id)},
            format="json",
        ).json()["items"][0]

        response = calendar_client.delete(
            f"{API}/calendar-exceptions/{created['id']}/"
        )
        assert response.status_code == 204

    def test_bad_date_is_a_clear_400(self, calendar_client):
        response = calendar_client.get(
            f"{API}/calendar-exceptions/", {"date_from": "вчера"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_anonymous_gets_nothing(self, api_client):
        assert api_client.get(f"{API}/calendar-exceptions/").status_code in (401, 403)
