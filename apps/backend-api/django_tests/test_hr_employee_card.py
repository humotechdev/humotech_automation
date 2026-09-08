"""Карточка сотрудника: журнал по дням, заявки и история одного человека.

Три расширения, и у каждого своя опасность.

**Журнал по дням не имеет права разойтись с присутствием.** Ночная
смена, открытая сессия и допуск опоздания — правила, у которых должен
быть ОДИН ответ. Поэтому строка дня собирается тем же кодом, что и
дашборд, а тесты ниже проверяют не арифметику, а то, что правила не
разъехались.

**Фильтр по человеку не расширяет доступ.** Он сужает уже разрешённое:
чужой сотрудник отвечает «не найден» или пустым набором, но никогда —
чужими данными.

**Историю отбирает сервер.** Клиент не знает идентификаторов назначений,
графиков и заявок заранее, и вычитывать ради этого журнал организации
он не должен.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from datetime import timezone as dt_timezone
from datetime import datetime

import pytest

from django_tests.test_hr_attendance import (
    DAY,
    closed_session,
    give_schedule,
    open_session,
    utc,
)
from humotech.attendance.hr import MAX_JOURNAL_DAYS, AttendanceHrService
from humotech.core.errors import NotFound, PermissionDenied, ValidationFailed

API = "/api/v1"

pytestmark = pytest.mark.django_db


def make_person(organization, office, *, number: str):
    """Сотрудник с основным назначением — как в conftest, но второй.

    Локально, а не в conftest: он нужен только здесь, ради проверки
    «чужие строки в выборку не попадают».
    """
    from humotech.employees.models import Employee, EmployeeAssignment

    person = Employee.objects.create(
        organization=organization,
        employee_number=number,
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2024, 2, 1),
        employment_status="ACTIVE",
    )
    assignment = EmployeeAssignment.objects.create(
        organization=organization,
        employee=person,
        office=office,
        employment_type="FULL_TIME",
        work_mode="ONSITE",
        is_primary=True,
        valid_from=date(2024, 2, 1),
    )
    return person, assignment


@pytest.fixture()
def service() -> AttendanceHrService:
    return AttendanceHrService()


@pytest.fixture()
def card_actor(make_actor, organization):
    return make_actor(
        organization,
        permissions=(
            "attendance.read", "employees.read", "offices.read",
            "absences.read", "audit.read",
        ),
    )


def day_of(report: dict, day: date) -> dict:
    return next(row for row in report["days"] if row["day"] == day)


# --- журнал по дням ----------------------------------------------------------


class TestDailyJournal:
    def test_day_is_decided_by_the_office_timezone(
        self, service, card_actor, organization, employee, office
    ):
        """Пояс офиса, а не UTC и не пояс смотрящего.

        Офис в Asia/Dushanbe (UTC+5). Отметка в 20:00 UTC — это 01:00
        СЛЕДУЮЩЕГО дня по офису, и попасть она обязана в тот день.
        """
        closed_session(
            organization, employee, office,
            started=utc(20), ended=utc(22),
        )
        report = service.daily(
            card_actor, employee.id, first=DAY, last=DAY + timedelta(days=1)
        )
        assert report["timezone"] == office.timezone
        assert day_of(report, DAY)["sessions"] == 0
        assert day_of(report, DAY + timedelta(days=1))["sessions"] == 1

    def test_night_shift_belongs_to_the_day_it_started(
        self, service, card_actor, organization, employee, office
    ):
        """Смена с 22:00 до 06:00 — это один день, а не два половинчатых.

        Так её считает присутствие, и журнал обязан совпадать с ним,
        а не спорить.
        """
        started = utc(19)  # 00:00 следующего дня по офису
        closed_session(
            organization, employee, office,
            started=started, ended=started + timedelta(hours=8),
        )
        report = service.daily(
            card_actor, employee.id, first=DAY, last=DAY + timedelta(days=2)
        )
        row = day_of(report, DAY + timedelta(days=1))
        assert row["sessions"] == 1
        assert row["seconds"] == 8 * 3600
        # Второй день смены пустой: сессию не делят пополам.
        assert day_of(report, DAY + timedelta(days=2))["sessions"] == 0

    def test_open_session_is_marked_and_counted_by_the_server(
        self, service, card_actor, organization, employee, office
    ):
        """У незакрытой сессии длительность считает сервер.

        Клиент, вычитающий «сейчас минус вход», получил бы другое число
        и разошёлся бы с дашбордом.
        """
        session = open_session(organization, employee, office, started=utc(4))
        report = service.daily(card_actor, employee.id, first=DAY, last=DAY)
        row = day_of(report, DAY)

        assert row["open_session_id"] == session.id
        assert row["state"] == "IN_OFFICE"
        assert row["last_exit_at"] is None
        assert report["totals"]["open_sessions"] == 1

    def test_late_counts_minutes_over_the_grace(
        self, service, card_actor, organization, employee, office
    ):
        """Допуск вычитается, а не служит порогом.

        Организация, разрешившая опаздывать на 15 минут, считает
        опозданием минуты СВЕРХ допуска, а не всю разницу целиком.
        """
        give_schedule(organization, employee, start=time(9, 0), grace=15)
        # 04:30 UTC = 09:30 по офису: полчаса позже начала.
        closed_session(
            organization, employee, office,
            started=utc(4, 30), ended=utc(13),
        )
        report = service.daily(card_actor, employee.id, first=DAY, last=DAY)
        assert day_of(report, DAY)["late_minutes"] == 15

    def test_no_schedule_day_off_and_absence_are_different_states(
        self, service, card_actor, organization, employee, office
    ):
        """Три разных ответа, а не один «не пришёл».

        Без графика сравнивать не с чем, и обвинять человека не в чем;
        выходной — это «и не должен был».
        """
        without = service.daily(card_actor, employee.id, first=DAY, last=DAY)
        assert day_of(without, DAY)["state"] == "NO_SCHEDULE"
        assert day_of(without, DAY)["late_minutes"] is None

        give_schedule(organization, employee, start=time(9, 0))
        saturday = date(2026, 3, 14)
        with_schedule = service.daily(
            card_actor, employee.id, first=DAY, last=saturday
        )
        assert day_of(with_schedule, DAY)["state"] == "NOT_COME"
        assert day_of(with_schedule, saturday)["state"] == "DAY_OFF"

    def test_totals_cover_the_whole_period_not_the_shown_rows(
        self, service, card_actor, organization, employee, office
    ):
        """Сводка, зависящая от длины таблицы, отвечает не на тот вопрос."""
        give_schedule(organization, employee, start=time(9, 0))
        closed_session(
            organization, employee, office, started=utc(4), ended=utc(8)
        )
        second = DAY + timedelta(days=1)
        closed_session(
            organization, employee, office,
            started=utc(4, day=second), ended=utc(9, day=second),
        )
        report = service.daily(
            card_actor, employee.id, first=DAY, last=DAY + timedelta(days=6)
        )
        assert len(report["days"]) == 7
        assert report["totals"]["days_with_marks"] == 2
        assert report["totals"]["seconds"] == (4 + 5) * 3600
        assert report["totals"]["working_days"] == 5  # пятидневка

    def test_period_longer_than_the_cap_is_refused(
        self, service, card_actor, employee
    ):
        with pytest.raises(ValidationFailed) as exc:
            service.daily(
                card_actor,
                employee.id,
                first=DAY,
                last=DAY + timedelta(days=MAX_JOURNAL_DAYS),
            )
        assert exc.value.details["max_days"] == MAX_JOURNAL_DAYS

    def test_reversed_period_is_refused(self, service, card_actor, employee):
        with pytest.raises(ValidationFailed):
            service.daily(
                card_actor, employee.id, first=DAY, last=DAY - timedelta(days=1)
            )

    def test_employee_outside_the_scope_is_not_found(
        self, service, make_actor, organization, employee, other_office
    ):
        """Не «нельзя», а «не найден»: иначе перебором считается чужой штат."""
        local = make_actor(
            organization,
            permissions=("attendance.read", "employees.read"),
            office=other_office,
        )
        with pytest.raises(NotFound):
            service.daily(local, employee.id, first=DAY, last=DAY)

    def test_reading_requires_attendance_read(
        self, service, nobody_actor, employee
    ):
        with pytest.raises(PermissionDenied):
            service.daily(nobody_actor, employee.id, first=DAY, last=DAY)

    def test_employee_without_assignment_says_so(
        self, service, card_actor, organization
    ):
        """Пустой журнал с объяснением, а не молчаливые нули."""
        from humotech.employees.models import Employee

        lonely = Employee.objects.create(
            organization=organization,
            employee_number="EMP-NOWHERE",
            first_name="Без",
            last_name="Назначения",
            hire_date=date(2024, 2, 1),
            employment_status="ACTIVE",
        )
        report = service.daily(card_actor, lonely.id, first=DAY, last=DAY)
        assert report["days"] == []
        assert report["totals"]["seconds"] == 0
        assert "назначения" in (report["note"] or "")


# --- HTTP --------------------------------------------------------------------


@pytest.fixture()
def card_client(api_client, make_user, organization):
    api_client.force_authenticate(
        user=make_user(
            organization,
            permissions=(
                "attendance.read", "employees.read", "offices.read",
                "absences.read", "audit.read",
            ),
        )
    )
    return api_client


class TestDailyHttp:
    def test_period_is_required(self, card_client, employee):
        answer = card_client.get(
            f"{API}/attendance/daily?employee_id={employee.id}"
        )
        assert answer.status_code == 400

    def test_employee_is_required(self, card_client):
        answer = card_client.get(
            f"{API}/attendance/daily?date_from=2026-03-10&date_to=2026-03-10"
        )
        assert answer.status_code == 400

    def test_journal_comes_back_with_totals(
        self, card_client, organization, employee, office
    ):
        closed_session(
            organization, employee, office, started=utc(4), ended=utc(8)
        )
        answer = card_client.get(
            f"{API}/attendance/daily?employee_id={employee.id}"
            f"&date_from={DAY}&date_to={DAY}"
        )
        assert answer.status_code == 200
        body = answer.json()
        assert body["totals"]["seconds"] == 4 * 3600
        assert body["days"][0]["office_name"] == office.name

    def test_without_permission_it_is_403(
        self, api_client, make_user, organization, employee
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("employees.read",))
        )
        answer = api_client.get(
            f"{API}/attendance/daily?employee_id={employee.id}"
            f"&date_from={DAY}&date_to={DAY}"
        )
        assert answer.status_code == 403


# --- заявки одного сотрудника ------------------------------------------------


class TestRequestsOfOneEmployee:
    def test_queue_narrows_to_the_employee(
        self, card_client, organization, employee, office
    ):
        """Фильтр сужает уже разрешённое, а не открывает доступ."""
        from humotech.absences.models import AbsenceRequest, AbsenceType

        kind, _ = AbsenceType.objects.get_or_create(
            organization=organization,
            code="VACATION",
            defaults={"name": "Отпуск", "is_active": True},
        )
        neighbour, _ = make_person(organization, office, number="EMP-NEIGHBOUR")
        for person in (employee, neighbour):
            AbsenceRequest.objects.create(
                organization=organization,
                employee=person,
                absence_type=kind,
                request_kind="CREATE",
                requested_start_at=datetime.combine(
                    DAY, time(0, 0), tzinfo=dt_timezone.utc
                ),
                requested_end_at=datetime.combine(
                    DAY, time(23, 59), tzinfo=dt_timezone.utc
                ),
                status="SUBMITTED",
                submitted_at=datetime.now(tz=dt_timezone.utc),
            )

        answer = card_client.get(
            f"{API}/requests?employee_id={employee.id}&kind=absence"
        )
        assert answer.status_code == 200
        rows = answer.json()["items"]
        assert len(rows) == 1
        assert rows[0]["absence"]["employee"]["id"] == str(employee.id)

    def test_foreign_employee_gives_nothing_not_someone_elses_requests(
        self, api_client, make_user, organization, employee, other_office
    ):
        api_client.force_authenticate(
            user=make_user(
                organization,
                permissions=("absences.read", "employees.read", "attendance.read"),
                office=other_office,
            )
        )
        answer = api_client.get(f"{API}/requests?employee_id={employee.id}")
        assert answer.status_code in (200, 404)
        if answer.status_code == 200:
            assert answer.json()["items"] == []


# --- история одного сотрудника -----------------------------------------------


class TestEmployeeAudit:
    def test_related_objects_are_gathered_by_the_server(
        self, card_client, card_actor, organization, employee, office
    ):
        """Назначение сотрудника попадает в его историю само.

        Клиент не передавал идентификатор назначения — его подобрал
        сервер.
        """
        from humotech.core.rbac import AuditTrail
        from humotech.employees.models import EmployeeAssignment

        assignment = EmployeeAssignment.objects.filter(
            employee=employee
        ).first()
        assert assignment is not None
        trail = AuditTrail()
        trail.record(
            card_actor,
            action="employee.assignment.change",
            entity_type="employee_assignments",
            entity_id=assignment.id,
            before={"office_id": None},
            after={"office_id": str(office.id)},
        )
        # Чужая запись того же типа — в историю попасть не должна.
        _, other = make_person(organization, office, number="EMP-STRANGER")
        trail.record(
            card_actor,
            action="employee.assignment.change",
            entity_type="employee_assignments",
            entity_id=other.id,
            before=None,
            after={"office_id": str(office.id)},
        )

        answer = card_client.get(f"{API}/audit-logs?employee_id={employee.id}")
        assert answer.status_code == 200
        found = {row["entity_id"] for row in answer.json()["items"]}
        assert str(assignment.id) in found
        assert str(other.id) not in found

    def test_foreign_employee_is_not_found(
        self, api_client, make_user, organization, employee, other_office
    ):
        api_client.force_authenticate(
            user=make_user(
                organization,
                permissions=("audit.read", "employees.read", "attendance.read"),
                office=other_office,
            )
        )
        answer = api_client.get(f"{API}/audit-logs?employee_id={employee.id}")
        assert answer.status_code == 404

    def test_audit_still_requires_its_own_permission(
        self, api_client, make_user, organization, employee
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("employees.read",))
        )
        answer = api_client.get(f"{API}/audit-logs?employee_id={employee.id}")
        assert answer.status_code == 403
