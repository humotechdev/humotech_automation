"""Сервис личных данных на живой базе: все двенадцать требуемых запросов.

Данные синтетические, создаются внутри теста и откатываются вместе с ним.
Реальные сотрудники и реальные HR-документы здесь не участвуют.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.modules.absences.models import (
    AbsenceRequest,
    AbsenceType,
    EmployeeAbsence,
    LeaveBalance,
)
from src.modules.ai_assistant.services.personal_data import PersonalIntent
from src.modules.ai_assistant.services.personal_data_service import (
    SqlPersonalDataQueryService,
)
from src.modules.qr_attendance.models import AttendanceEvent, AttendanceSession

pytestmark = pytest.mark.usefixtures("engine")

DUSHANBE = ZoneInfo("Asia/Dushanbe")
# 3 сентября 2026, 15:00 по Душанбе = 10:00 UTC
NOW = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)


@pytest.fixture()
def service(db) -> SqlPersonalDataQueryService:
    return SqlPersonalDataQueryService(db)


def add_event(db, organization, office, employee, qr_point, occurred_at, kind):
    event = AttendanceEvent(
        organization_id=organization.id,
        employee_id=employee.id,
        office_id=office.id,
        qr_point_id=qr_point.id,
        event_type=kind,
        source="QR",
        verification_status="ACCEPTED",
        occurred_at=occurred_at,
        received_at=occurred_at,
    )
    db.add(event)
    db.flush()
    return event


def add_session(db, organization, office, employee, qr_point, *, started, ended=None):
    entry = add_event(db, organization, office, employee, qr_point, started, "ENTRY")
    exit_event = None
    duration = None
    status = "OPEN"
    if ended is not None:
        exit_event = add_event(
            db, organization, office, employee, qr_point, ended, "EXIT"
        )
        duration = int((ended - started).total_seconds())
        status = "CLOSED"
    work = AttendanceSession(
        organization_id=organization.id,
        employee_id=employee.id,
        office_id=office.id,
        entry_event_id=entry.id,
        exit_event_id=exit_event.id if exit_event else None,
        started_at=started,
        ended_at=ended,
        duration_seconds=duration,
        status=status,
    )
    db.add(work)
    db.flush()
    return work


def add_absence(db, organization, employee, *, code, name, start, end, status):
    absence_type = db.query(AbsenceType).filter(
        AbsenceType.organization_id == organization.id, AbsenceType.code == code
    ).one_or_none()
    if absence_type is None:
        absence_type = AbsenceType(
            organization_id=organization.id, code=code, name=name
        )
        db.add(absence_type)
        db.flush()

    request = AbsenceRequest(
        organization_id=organization.id,
        employee_id=employee.id,
        absence_type_id=absence_type.id,
        request_kind="CREATE",
        status="APPROVED",
    )
    db.add(request)
    db.flush()

    absence = EmployeeAbsence(
        organization_id=organization.id,
        employee_id=employee.id,
        absence_type_id=absence_type.id,
        origin_request_id=request.id,
        start_at=start,
        end_at=end,
        status=status,
    )
    db.add(absence)
    db.flush()
    return absence_type, absence


def ask(service, employee, question):
    return service.answer(
        employee_id=employee.id, question=question, language="ru", now=NOW
    )


# --------------------------------------------------------------- приход и уход

def test_arrival_today(db, organization, office, employee, qr_point, service):
    add_session(
        db, organization, office, employee, qr_point,
        started=NOW - timedelta(hours=6), ended=NOW - timedelta(hours=1),
    )
    result = ask(service, employee, "Когда я сегодня пришёл?")

    assert result.available
    assert result.intent is PersonalIntent.ARRIVAL_TODAY
    # 10:00 UTC - 6ч = 04:00 UTC = 09:00 по Душанбе
    assert "09:00" in result.text


def test_departure_today(db, organization, office, employee, qr_point, service):
    add_session(
        db, organization, office, employee, qr_point,
        started=NOW - timedelta(hours=6), ended=NOW - timedelta(hours=1),
    )
    result = ask(service, employee, "Когда я ушёл?")

    assert result.available
    assert "14:00" in result.text  # 09:00 UTC = 14:00 по Душанбе


def test_open_session_never_invents_a_departure_time(
    db, organization, office, employee, qr_point, service
):
    """Главное правило: у незакрытой сессии времени ухода НЕТ."""
    add_session(
        db, organization, office, employee, qr_point,
        started=NOW - timedelta(hours=3),
    )
    result = ask(service, employee, "Когда я ушёл?")

    assert result.available is False
    assert "не отмечали" in result.text
    assert "open_since_local" in result.data


def test_no_marks_today_is_reported_honestly(db, employee, service):
    result = ask(service, employee, "Когда я сегодня пришёл?")
    assert result.available is False
    assert "нет" in result.text.lower()


# ------------------------------------------------------------- нахожусь в офисе

def test_in_office_now_true(db, organization, office, employee, qr_point, service):
    add_session(
        db, organization, office, employee, qr_point,
        started=NOW - timedelta(hours=2),
    )
    result = ask(service, employee, "Нахожусь ли я сейчас в офисе?")

    assert result.data["in_office"] is True
    assert result.data["minutes"] == 120
    assert office.name in result.text


def test_in_office_now_false(db, organization, office, employee, qr_point, service):
    add_session(
        db, organization, office, employee, qr_point,
        started=NOW - timedelta(hours=6), ended=NOW - timedelta(hours=1),
    )
    result = ask(service, employee, "Я сейчас в офисе?")
    assert result.data["in_office"] is False


# ------------------------------------------------------------------- длительность

def test_duration_today_sums_sessions(
    db, organization, office, employee, qr_point, service
):
    add_session(
        db, organization, office, employee, qr_point,
        started=NOW - timedelta(hours=8), ended=NOW - timedelta(hours=5),
    )
    add_session(
        db, organization, office, employee, qr_point,
        started=NOW - timedelta(hours=4), ended=NOW - timedelta(hours=2),
    )
    result = ask(service, employee, "Сколько времени я провёл в офисе сегодня?")

    assert result.data["minutes"] == 300  # 3 ч + 2 ч
    assert result.data["session_open"] is False


def test_duration_today_marks_open_session(
    db, organization, office, employee, qr_point, service
):
    add_session(
        db, organization, office, employee, qr_point,
        started=NOW - timedelta(hours=2),
    )
    result = ask(service, employee, "Сколько времени я провёл в офисе сегодня?")

    assert result.data["session_open"] is True
    assert "ещё открыта" in result.text


# ------------------------------------------------------------ неделя и месяц

def test_hours_for_week(db, organization, office, employee, qr_point, service):
    # 3 сентября 2026 — четверг; понедельник этой недели — 31 августа
    for days_ago in (0, 1, 2):
        day_start = NOW - timedelta(days=days_ago, hours=6)
        add_session(
            db, organization, office, employee, qr_point,
            started=day_start, ended=day_start + timedelta(hours=8),
        )
    result = ask(service, employee, "Сколько часов получилось за неделю?")

    assert result.available
    assert result.data["days"] == 3
    assert result.data["minutes"] == 3 * 8 * 60


def test_hours_for_month_excludes_previous_month(
    db, organization, office, employee, qr_point, service
):
    # 25 августа — прошлый месяц, считаться не должно
    august = datetime(2026, 8, 25, 5, 0, tzinfo=timezone.utc)
    add_session(
        db, organization, office, employee, qr_point,
        started=august, ended=august + timedelta(hours=8),
    )
    september = NOW - timedelta(hours=6)
    add_session(
        db, organization, office, employee, qr_point,
        started=september, ended=september + timedelta(hours=5),
    )
    result = ask(service, employee, "Сколько часов получилось за месяц?")

    assert result.data["days"] == 1
    assert result.data["minutes"] == 5 * 60


# ------------------------------------------------------------------- отсутствия

def test_absence_days_lists_current_month(db, organization, employee, service):
    add_absence(
        db, organization, employee, code="SICK_LEAVE", name="Больничный",
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 9, 2, 23, 59, tzinfo=timezone.utc),
        status="COMPLETED",
    )
    result = ask(service, employee, "Какие дни я отсутствовал?")

    assert result.available
    assert len(result.data["items"]) == 1
    assert result.data["items"][0]["type"] == "Больничный"


def test_absence_days_empty_is_honest(db, employee, service):
    result = ask(service, employee, "Какие дни я отсутствовал?")
    assert result.available
    assert result.data["items"] == []


# -------------------------------------------------------- больничный и отпуск

def test_sick_leave_status_and_dates(db, organization, employee, service):
    add_absence(
        db, organization, employee, code="SICK_LEAVE", name="Больничный",
        start=NOW - timedelta(days=1), end=NOW + timedelta(days=2),
        status="ACTIVE",
    )
    status = ask(service, employee, "Какой статус моего больничного?")
    dates = ask(service, employee, "Какие даты моего больничного?")

    assert status.available and "идёт сейчас" in status.text
    assert dates.available and dates.data["from"] == "2026-09-02"
    assert dates.data["to"] == "2026-09-05"


def test_vacation_status_and_dates(db, organization, employee, service):
    add_absence(
        db, organization, employee, code="ANNUAL_LEAVE", name="Ежегодный отпуск",
        start=NOW + timedelta(days=10), end=NOW + timedelta(days=20),
        status="PLANNED",
    )
    status = ask(service, employee, "Какой статус моего отпуска?")
    dates = ask(service, employee, "Какие даты моего отпуска?")

    assert "запланирован" in status.text
    assert dates.data["from"] == "2026-09-13"


def test_missing_sick_leave_is_reported_honestly(db, employee, service):
    result = ask(service, employee, "Какой статус моего больничного?")
    assert result.available is False
    assert "нет" in result.text.lower()


# ----------------------------------------------------------------- остаток дней

def test_leave_balance_without_schedule_reports_hours_only(
    db, organization, employee, service
):
    """Без графика пересчитать минуты в дни нельзя — и мы не выдумываем."""
    absence_type, _ = add_absence(
        db, organization, employee, code="ANNUAL_LEAVE", name="Ежегодный отпуск",
        start=NOW + timedelta(days=30), end=NOW + timedelta(days=40),
        status="PLANNED",
    )
    db.add(
        LeaveBalance(
            organization_id=organization.id,
            employee_id=employee.id,
            absence_type_id=absence_type.id,
            year=2026,
            allocated_minutes=28 * 8 * 60,
            used_minutes=8 * 8 * 60,
            reserved_minutes=0,
            adjustment_minutes=0,
        )
    )
    db.flush()

    result = ask(service, employee, "Сколько дней отпуска осталось?")

    assert result.available
    assert result.data["available_minutes"] == 20 * 8 * 60
    assert "available_days" not in result.data
    assert "график" in result.text


def test_leave_balance_with_schedule_converts_to_days(
    db, organization, employee, service
):
    from src.modules.schedules.models import (
        EmployeeScheduleAssignment,
        ScheduleDay,
        WorkSchedule,
    )

    schedule = WorkSchedule(
        organization_id=organization.id,
        name="Пятидневка",
        timezone="Asia/Dushanbe",
        weekly_minutes=5 * 8 * 60,
        status="ACTIVE",
    )
    db.add(schedule)
    db.flush()
    for weekday in range(1, 6):
        db.add(
            ScheduleDay(
                schedule_id=schedule.id, weekday=weekday, is_working_day=True,
                start_time=__import__("datetime").time(9, 0),
                end_time=__import__("datetime").time(18, 0),
            )
        )
    db.add(
        EmployeeScheduleAssignment(
            organization_id=organization.id,
            employee_id=employee.id,
            schedule_id=schedule.id,
            valid_from=date(2026, 1, 1),
        )
    )
    db.flush()

    absence_type, _ = add_absence(
        db, organization, employee, code="ANNUAL_LEAVE", name="Ежегодный отпуск",
        start=NOW + timedelta(days=30), end=NOW + timedelta(days=40),
        status="PLANNED",
    )
    db.add(
        LeaveBalance(
            organization_id=organization.id,
            employee_id=employee.id,
            absence_type_id=absence_type.id,
            year=2026,
            allocated_minutes=28 * 8 * 60,
            used_minutes=0,
            reserved_minutes=2 * 8 * 60,
            adjustment_minutes=0,
        )
    )
    db.flush()

    result = ask(service, employee, "Сколько дней отпуска осталось?")

    assert result.data["day_minutes"] == 8 * 60
    assert result.data["available_days"] == 26.0


def test_missing_balance_is_reported_honestly(db, employee, service):
    result = ask(service, employee, "Сколько дней отпуска осталось?")
    assert result.available is False
    assert "не заведён" in result.text


# ------------------------------------------------------------------- изоляция

def test_employee_never_sees_another_employees_sessions(
    db, organization, office, employee, qr_point, service
):
    """Фильтр по employee_id: чужие сессии не попадают в ответ."""
    from src.modules.employees.models import Employee, EmployeeAssignment

    other = Employee(
        organization_id=organization.id,
        employee_number="EMP-OTHER",
        first_name="Пётр", last_name="Петров",
        hire_date=date(2025, 1, 1), employment_status="ACTIVE",
    )
    db.add(other)
    db.flush()
    db.add(
        EmployeeAssignment(
            organization_id=organization.id, employee_id=other.id,
            office_id=office.id, employment_type="FULL_TIME",
            work_mode="ONSITE", is_primary=True, valid_from=date(2025, 1, 1),
        )
    )
    db.flush()

    add_session(
        db, organization, office, other, qr_point,
        started=NOW - timedelta(hours=7), ended=NOW - timedelta(hours=1),
    )

    result = ask(service, employee, "Сколько времени я провёл в офисе сегодня?")
    assert result.available is False, "видны чужие сессии"


def test_hr_access_requires_permission(db, organization, employee, office):
    """HR получает данные сотрудника только через существующие permissions."""
    from src.modules.ai_assistant.use_cases.crm import (
        Actor,
        KnowledgeAdminUseCases,
        PermissionDenied,
    )
    from src.modules.roles.models import Permission, Role, RolePermission, UserRoleScope
    from src.modules.users.models import User

    user = User(
        organization_id=organization.id,
        email=f"hr-{uuid.uuid4().hex[:6]}@humotech.tj",
        password_hash="argon2:stub", status="ACTIVE",
    )
    role = Role(code="LIMITED", name="Ограниченная роль", is_system=False,
                organization_id=organization.id)
    db.add_all([user, role])
    db.flush()
    db.add(
        UserRoleScope(
            organization_id=organization.id, user_id=user.id, role_id=role.id,
        )
    )
    db.flush()

    crm = KnowledgeAdminUseCases(db)
    actor = Actor(user_id=user.id, organization_id=organization.id)

    # без attendance.read доступ закрыт
    with pytest.raises(PermissionDenied, match="attendance.read"):
        crm.employee_personal_data(actor, employee.id, question="Когда я пришёл?")

    # выдаём разрешение — доступ появляется
    perm = Permission(code="attendance.read", name="Просмотр отметок")
    db.add(perm)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.flush()

    answer = crm.employee_personal_data(
        actor, employee.id, question="Когда я сегодня пришёл?"
    )
    assert answer.intent is PersonalIntent.ARRIVAL_TODAY
