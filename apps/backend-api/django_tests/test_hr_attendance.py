"""Посещаемость для кадровика: область видимости, состояния, исправления.

Три вещи здесь важнее остальных, и каждая проверяется отдельно.

**Сырое событие не меняется.** Ни один метод сервиса не переписывает
`AttendanceEvent`. Проверяется не наличием метода, а тем, что после
одобрения исправления событие осталось прежним: спор «во сколько человек
пришёл» разрешается только записью, которую нельзя было подделать.

**Пустая область — это «ничего».** Отдельный тест на каждый список:
именно здесь легче всего написать `if visible:` и молча показать
всю организацию тому, у кого прав нет.

**Ноль и «неизвестно» — разные ответы.** Нет графика — нет и суждения
об опоздании; `late_minutes` остаётся `None`, а не становится нулём.
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4
from datetime import timezone as dt_timezone

import pytest

from django_tests.conftest import make_qr_point
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import (
    AttendanceCorrectionRequest,
    AttendanceEvent,
    AttendanceSession,
)
from humotech.core.errors import Conflict, NotFound, PermissionDenied
from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)

DAY = date(2026, 3, 10)  # вторник


def utc(hour: int, minute: int = 0, *, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=dt_timezone.utc)


@pytest.fixture()
def service() -> AttendanceHrService:
    return AttendanceHrService()


@pytest.fixture()
def attendance_actor(make_actor, organization):
    return make_actor(
        organization,
        permissions=(
            "attendance.read", "attendance.correct", "attendance.manual",
            "employees.read", "offices.read",
        ),
    )


def event(organization, employee, office, *, kind, at, point=None):
    """Сессия без события в базе существовать не может: `entry_event_id`
    объявлен NOT NULL. Поэтому и в тестах сессия собирается из событий,
    как в бою, а не создаётся сама по себе.

    Точка обязательна для `source = QR`: это проверяет ограничение базы
    `ck_attendance_events_qr_requires_point` — без точки офис отметки
    определить нечем.
    """
    return AttendanceEvent.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        qr_point=point or make_qr_point(organization, office,
                                        code=f"P-{uuid4().hex[:8]}"),
        event_type=kind,
        source="QR",
        verification_status="ACCEPTED",
        occurred_at=at,
    )


def open_session(organization, employee, office, *, started=None):
    started = started or utc(4)
    entry = event(organization, employee, office, kind="ENTRY", at=started)
    return AttendanceSession.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        entry_event=entry,
        started_at=started,
        status="OPEN",
    )


def closed_session(organization, employee, office, *, started=None, ended=None):
    started = started or utc(4)
    ended = ended or utc(13)
    entry = event(organization, employee, office, kind="ENTRY", at=started)
    exit_ = event(organization, employee, office, kind="EXIT", at=ended)
    return AttendanceSession.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        entry_event=entry,
        exit_event=exit_,
        started_at=started,
        ended_at=ended,
        duration_seconds=int((ended - started).total_seconds()),
        status="CLOSED",
    )


def give_schedule(organization, employee, *, start=time(9, 0), working=True,
                  grace=0):
    schedule = WorkSchedule.objects.create(
        organization=organization,
        name="Пятидневка",
        timezone="Asia/Dushanbe",
        weekly_minutes=40 * 60,
        late_grace_minutes=grace,
        status="ACTIVE",
    )
    for weekday in range(1, 8):
        is_working = working and weekday <= 5
        ScheduleDay.objects.create(
            schedule=schedule,
            weekday=weekday,
            is_working_day=is_working,
            start_time=start if is_working else None,
            end_time=time(18, 0) if is_working else None,
        )
    EmployeeScheduleAssignment.objects.create(
        organization=organization,
        employee=employee,
        schedule=schedule,
        valid_from=date(2024, 1, 1),
    )
    return schedule


# --- присутствие -------------------------------------------------------------


@pytest.mark.django_db
class TestPresence:
    def test_open_session_shows_as_in_office(
        self, service, attendance_actor, organization, employee, office
    ):
        open_session(organization, employee, office)

        report = service.presence(attendance_actor, day=DAY, office_id=office.id)

        assert len(report.rows) == 1
        row = report.rows[0]
        assert row.state == "IN_OFFICE"
        assert row.open_session_id is not None
        # Незакрытой сессии не назначается выдуманное время выхода.
        assert row.last_exit_at is None

    def test_closed_session_shows_as_left(
        self, service, attendance_actor, organization, employee, office
    ):
        closed_session(organization, employee, office)

        report = service.presence(attendance_actor, day=DAY, office_id=office.id)

        row = report.rows[0]
        assert row.state == "LEFT"
        assert row.seconds == 9 * 3600
        assert row.open_session_id is None

    def test_without_schedule_absence_is_not_an_accusation(
        self, service, attendance_actor, office
    ):
        # Графика нет — значит, сказать «не пришёл на работу» не о чем.
        # Отдельное состояние, а не прогул: ненастроенный справочник
        # не должен превращаться в обвинение человеку.
        report = service.presence(attendance_actor, day=DAY, office_id=office.id)
        assert report.rows == [] or report.rows[0].state == "NO_SCHEDULE"

    def test_no_schedule_means_no_lateness_judgement(
        self, service, attendance_actor, organization, employee, office
    ):
        closed_session(organization, employee, office, started=utc(6))

        row = service.presence(
            attendance_actor, day=DAY, office_id=office.id
        ).rows[0]

        # None, а не ноль: ноль читался бы как «пришёл ровно вовремя».
        assert row.late_minutes is None
        assert row.scheduled_start is None

    def test_lateness_counted_against_schedule(
        self, service, attendance_actor, organization, employee, office
    ):
        give_schedule(organization, employee, start=time(9, 0))
        # Пояс офиса из фикстуры; вход через два часа после начала смены.
        started = _local(office, DAY, time(11, 0))
        closed_session(organization, employee, office, started=started)

        row = service.presence(
            attendance_actor, day=DAY, office_id=office.id
        ).rows[0]

        assert row.state == "LEFT"
        assert row.late_minutes == 120

    def test_on_time_arrival_is_zero_not_null(
        self, service, attendance_actor, organization, employee, office
    ):
        give_schedule(organization, employee, start=time(9, 0))
        closed_session(
            organization, employee, office, started=_local(office, DAY, time(9, 0))
        )

        row = service.presence(
            attendance_actor, day=DAY, office_id=office.id
        ).rows[0]

        # Здесь ноль осмыслен: сравнение состоялось и дало ноль.
        assert row.late_minutes == 0

    def test_grace_period_from_the_schedule_is_honoured(
        self, service, attendance_actor, organization, employee, office
    ):
        """Допуск — правило организации, а не поблажка от сервера.

        График с допуском в 15 минут означает, что пришедший в 09:10
        не опоздал. Считать его опоздавшим значило бы спорить с
        собственными настройками компании.
        """
        give_schedule(organization, employee, start=time(9, 0), grace=15)
        closed_session(
            organization, employee, office,
            started=_local(office, DAY, time(9, 10)),
        )

        row = service.presence(
            attendance_actor, day=DAY, office_id=office.id
        ).rows[0]
        assert row.late_minutes == 0

        # А сверх допуска считаются минуты СВЕРХ него, а не все подряд.
        AttendanceSession.objects.all().delete()
        closed_session(
            organization, employee, office,
            started=_local(office, DAY, time(9, 40)),
        )
        row = service.presence(
            attendance_actor, day=DAY, office_id=office.id
        ).rows[0]
        assert row.late_minutes == 25

    def test_early_arrival_is_not_negative_lateness(
        self, service, attendance_actor, organization, employee, office
    ):
        give_schedule(organization, employee, start=time(9, 0))
        closed_session(
            organization, employee, office, started=_local(office, DAY, time(8, 30))
        )

        row = service.presence(
            attendance_actor, day=DAY, office_id=office.id
        ).rows[0]

        assert row.late_minutes == 0

    def test_counts_feed_the_dashboard(
        self, service, attendance_actor, organization, employee, office
    ):
        give_schedule(organization, employee)
        open_session(organization, employee, office)

        report = service.presence(attendance_actor, day=DAY, office_id=office.id)

        counts = report.counts()
        assert counts["IN_OFFICE"] == 1
        assert counts["NOT_COME"] == 0
        # Все состояния присутствуют ключами, даже нулевые: дашборд не
        # должен разбирать отсутствие ключа как ноль.
        assert set(counts) >= {"IN_OFFICE", "LEFT", "NOT_COME", "SICK_LEAVE"}

    def test_night_shift_belongs_to_the_day_it_started(
        self, service, attendance_actor, organization, employee, office
    ):
        # Смена с вечера DAY до утра следующего дня целиком относится к DAY.
        started = _local(office, DAY, time(22, 0))
        ended = _local(office, date(2026, 3, 11), time(6, 0))
        closed_session(organization, employee, office, started=started, ended=ended)

        today = service.presence(attendance_actor, day=DAY, office_id=office.id)
        tomorrow = service.presence(
            attendance_actor, day=date(2026, 3, 11), office_id=office.id
        )

        assert today.rows[0].seconds == 8 * 3600
        assert tomorrow.rows[0].seconds == 0

    def test_unknown_state_filter_is_rejected(
        self, service, attendance_actor, office
    ):
        from humotech.core.errors import ValidationFailed

        with pytest.raises(ValidationFailed):
            service.presence(attendance_actor, day=DAY, state="ЧТО_ТО")


# --- область видимости -------------------------------------------------------


@pytest.mark.django_db
class TestScope:
    def test_office_admin_does_not_see_another_office(
        self, service, make_actor, organization, employee, office, other_office
    ):
        closed_session(organization, employee, office)
        stranger = make_actor(
            organization,
            permissions=("attendance.read",),
            office=other_office,
        )

        report = service.presence(stranger, day=DAY)

        assert report.rows == []

    def test_empty_scope_gives_nothing_not_everything(
        self, service, make_actor, organization, employee, office
    ):
        """Разрешение есть, а видеть нечего — это «ничего», а не «всё».

        Область выдана на регион, в котором пока нет ни одного офиса:
        так бывает у нового региона, куда HR назначили заранее. Проверка
        вида `if visible_offices:` в этом месте молча отдала бы всю
        организацию.
        """
        from humotech.regions.models import Region

        closed_session(organization, employee, office)
        empty_region = Region.objects.create(
            organization=organization, code="EMPTY", name="Пустой", status="ACTIVE"
        )
        nobody = make_actor(
            organization, permissions=("attendance.read",), region=empty_region
        )

        assert service.presence(nobody, day=DAY).rows == []
        assert service.events(nobody).items == []
        assert service.sessions(nobody).items == []

    def test_regional_hr_sees_only_own_region(
        self, service, make_actor, organization, employee, office,
        other_office, region,
    ):
        closed_session(organization, employee, office)
        closed_session(organization, employee, other_office)

        regional = make_actor(
            organization, permissions=("attendance.read",), region=region
        )
        sessions = service.sessions(regional)

        assert {s.office_id for s in sessions.items} == {office.id}

    def test_foreign_office_filter_is_refused(
        self, service, attendance_actor, foreign_office
    ):
        with pytest.raises((PermissionDenied, NotFound)):
            service.presence(attendance_actor, day=DAY, office_id=foreign_office.id)

    def test_events_scoped_by_office_of_the_event_not_of_the_employee(
        self, service, make_actor, organization, employee, office, other_office
    ):
        # Отметка сделана в чужом офисе, сотрудник числится в своём.
        event(organization, employee, other_office, kind="ENTRY", at=utc(4))
        local_admin = make_actor(
            organization, permissions=("attendance.read",), office=office
        )

        # Администратор своего офиса чужую отметку не видит…
        assert service.events(local_admin).items == []
        # …а администратор офиса, где она сделана, — видит.
        other_admin = make_actor(
            organization, permissions=("attendance.read",), office=other_office
        )
        assert len(service.events(other_admin).items) == 1


# --- исправления -------------------------------------------------------------


@pytest.mark.django_db
class TestCorrections:
    def _request(self, organization, employee, session):
        return AttendanceCorrectionRequest.objects.create(
            organization=organization,
            employee=employee,
            attendance_session=session,
            requested_entry_at=utc(3),
            reason="Забыл отметиться на входе",
            status="SUBMITTED",
            submitted_at=utc(14),
        )

    def test_approval_never_touches_the_raw_event(
        self, service, attendance_actor, organization, employee, office
    ):
        session = closed_session(organization, employee, office)
        entry = session.entry_event
        request = self._request(organization, employee, session)

        service.review_correction(attendance_actor, request.id, decision="approve")

        entry.refresh_from_db()
        # Событие ровно то же самое: спор о времени прихода решается тем,
        # что переписать эту строку было нельзя.
        assert entry.occurred_at == utc(4)
        assert entry.source == "QR"

        session.refresh_from_db()
        assert session.started_at == utc(3)
        assert session.status == "CORRECTED"

    def test_second_decision_is_refused(
        self, service, attendance_actor, organization, employee, office
    ):
        session = closed_session(organization, employee, office)
        request = self._request(organization, employee, session)

        service.review_correction(attendance_actor, request.id, decision="reject")
        with pytest.raises(Conflict):
            service.review_correction(attendance_actor, request.id, decision="approve")

    def test_decision_is_written_to_the_audit_log(
        self, service, attendance_actor, organization, employee, office
    ):
        from humotech.audit.models import AuditLog

        session = closed_session(organization, employee, office)
        request = self._request(organization, employee, session)

        service.review_correction(
            attendance_actor, request.id, decision="approve", comment="Проверено"
        )

        entry = AuditLog.objects.get(action="attendance.correction.reviewed")
        assert entry.actor_user_id == attendance_actor.user_id
        assert entry.new_values["status"] == "APPROVED"
        assert entry.old_values["status"] == "SUBMITTED"

    def test_correction_requires_its_own_permission(
        self, service, make_actor, organization, employee, office
    ):
        session = closed_session(organization, employee, office)
        request = self._request(organization, employee, session)
        reader = make_actor(organization, permissions=("attendance.read",))

        with pytest.raises(PermissionDenied):
            service.review_correction(reader, request.id, decision="approve")


@pytest.mark.django_db
class TestManualEvent:
    def test_manual_event_is_a_new_row_with_its_reason(
        self, service, attendance_actor, organization, employee, office
    ):
        event = service.manual_event(
            attendance_actor,
            employee_id=employee.id,
            office_id=office.id,
            event_type="ENTRY",
            occurred_at=utc(4),
            reason="Сломан экран у входа",
        )

        assert event.source == "MANUAL"
        assert event.event_metadata["reason"] == "Сломан экран у входа"
        assert event.event_metadata["created_by_user_id"] == str(
            attendance_actor.user_id
        )

    def test_reason_is_required(
        self, service, attendance_actor, organization, employee, office
    ):
        from humotech.core.errors import ValidationFailed

        with pytest.raises(ValidationFailed):
            service.manual_event(
                attendance_actor,
                employee_id=employee.id,
                office_id=office.id,
                event_type="ENTRY",
                occurred_at=utc(4),
                reason="   ",
            )

    def test_manual_event_requires_its_own_permission(
        self, make_actor, organization, employee, office
    ):
        service = AttendanceHrService()
        reader = make_actor(
            organization, permissions=("attendance.read", "attendance.correct")
        )

        with pytest.raises(PermissionDenied):
            service.manual_event(
                reader,
                employee_id=employee.id,
                office_id=office.id,
                event_type="ENTRY",
                occurred_at=utc(4),
                reason="Проверка",
            )


# --- вспомогательное ---------------------------------------------------------


def _local(office, day: date, at: time) -> datetime:
    """Момент в поясе офиса, приведённый к UTC."""
    from humotech.core.timeframes import office_zone

    return datetime.combine(day, at, tzinfo=office_zone(office)).astimezone(
        dt_timezone.utc
    )


# --- HTTP --------------------------------------------------------------------

API = "/api/v1"


@pytest.fixture()
def attendance_client(api_client, make_user, organization):
    user = make_user(
        organization,
        permissions=(
            "attendance.read", "attendance.correct", "attendance.manual",
        ),
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.mark.django_db
class TestHttp:
    def test_presence_returns_counts_next_to_rows(
        self, attendance_client, organization, employee, office
    ):
        open_session(organization, employee, office)

        response = attendance_client.get(
            f"{API}/attendance/presence",
            {"date": DAY.isoformat(), "office_id": str(office.id)},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["date"] == DAY.isoformat()
        assert body["timezone"] == "Asia/Dushanbe"
        assert body["counts"]["IN_OFFICE"] == 1
        assert body["total"] == 1
        assert body["items"][0]["state"] == "IN_OFFICE"

    def test_events_are_read_only_over_http(
        self, attendance_client, organization, employee, office
    ):
        entry = event(organization, employee, office, kind="ENTRY", at=utc(4))

        assert attendance_client.get(f"{API}/attendance/events").status_code == 200
        # Ни POST, ни PATCH, ни DELETE на журнале событий не существует:
        # сырую отметку нельзя переписать даже кадровику.
        for method in ("post", "patch", "delete"):
            response = getattr(attendance_client, method)(
                f"{API}/attendance/events", {"id": str(entry.id)}
            )
            assert response.status_code in (403, 404, 405)

    def test_manual_event_requires_a_reason_over_http(
        self, attendance_client, employee, office
    ):
        response = attendance_client.post(
            f"{API}/attendance/manual",
            {
                "employee_id": str(employee.id),
                "office_id": str(office.id),
                "event_type": "ENTRY",
                "occurred_at": utc(4).isoformat(),
            },
            format="json",
        )
        assert response.status_code == 400

    def test_bad_date_is_a_clear_400_not_a_500(self, attendance_client):
        response = attendance_client.get(
            f"{API}/attendance/presence", {"date": "вчера"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_anonymous_gets_nothing(self, api_client):
        assert api_client.get(f"{API}/attendance/presence").status_code in (401, 403)


# --- число запросов ----------------------------------------------------------


@pytest.mark.django_db
class TestQueryCount:
    def test_presence_does_not_grow_with_the_number_of_employees(
        self, service, attendance_actor, organization, office, django_assert_max_num_queries
    ):
        """Число запросов не зависит от размера смены.

        Наивная реализация делает по запросу на человека — на двенадцати
        офисах это тысячи запросов на один экран дашборда. Проверяется
        именно постоянство: сначала на одном сотруднике, потом на десяти,
        и оба раза бюджет один.
        """
        from humotech.employees.models import Employee, EmployeeAssignment

        def hire(number: int):
            person = Employee.objects.create(
                organization=organization,
                employee_number=f"Q-{number:04d}",
                first_name="Тест",
                last_name=f"Сотрудник{number}",
                hire_date=date(2024, 1, 1),
                employment_status="ACTIVE",
            )
            EmployeeAssignment.objects.create(
                organization=organization,
                employee=person,
                office=office,
                employment_type="FULL_TIME",
                work_mode="ONSITE",
                is_primary=True,
                valid_from=date(2024, 1, 1),
            )
            give_schedule(organization, person)
            closed_session(organization, person, office)
            return person

        hire(1)
        with django_assert_max_num_queries(12):
            service.presence(attendance_actor, day=DAY, office_id=office.id)

        for number in range(2, 12):
            hire(number)
        # Тот же бюджет на вдесятеро большем составе.
        with django_assert_max_num_queries(12):
            report = service.presence(attendance_actor, day=DAY, office_id=office.id)
        assert len(report.rows) == 11
