"""Выездная работа: личный ответ, область доступа и единый расчёт дня."""

from datetime import datetime, time, timedelta, timezone as utc_timezone

import pytest
from django.utils import timezone

from django_tests.conftest import link_telegram
from django_tests.test_hr_attendance import give_schedule, open_session
from humotech.attendance.field_work import FieldWorkService
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import FieldWorkRequest
from humotech.attendance.statistics import for_period
from humotech.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from humotech.core.timeframes import office_zone
from humotech.telegram.identity import resolve_by_telegram_user_id

pytestmark = pytest.mark.django_db


@pytest.fixture()
def field_setup(organization, employee, office, make_actor, telegram_settings,
                monkeypatch):
    actor = make_actor(organization, permissions=("attendance.read", "attendance.correct"))
    give_schedule(organization, employee)
    link_telegram(employee)
    day = timezone.now().astimezone(office_zone(office)).date()
    while day.weekday() >= 5:
        day += timedelta(days=1)
    fixed_now = datetime.combine(day, time(12), tzinfo=office_zone(office))
    monkeypatch.setattr(timezone, "now", lambda: fixed_now.astimezone(utc_timezone.utc))
    return actor, day


def test_confirm_credits_schedule_without_fake_marks(field_setup, employee):
    actor, day = field_setup
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)
    assert row.status == "PENDING"
    assert AttendanceHrService().daily(actor, employee.id, first=day, last=day)["days"][0]["state"] == "FIELD_WORK_PENDING"
    decided = FieldWorkService().decide(row.id, telegram_user_id=777_000_111, decision="CONFIRM")
    assert decided.status == "CONFIRMED"
    assert FieldWorkService().decide(row.id, telegram_user_id=777_000_111, decision="CONFIRM").id == row.id
    attendance = AttendanceHrService().daily(actor, employee.id, first=day, last=day)
    assert attendance["days"][0]["state"] == "FIELD_WORK"
    assert attendance["days"][0]["seconds"] == 8 * 3600
    assert attendance["days"][0]["sessions"] == 0
    personal = for_period(resolve_by_telegram_user_id(777_000_111), day, day)
    assert personal.days[0].attended and not personal.days[0].missed
    assert personal.seconds == row.norm_seconds
    from humotech.attendance.models import AttendanceEvent, AttendanceSession
    assert not AttendanceEvent.objects.filter(employee=employee).exists()
    assert not AttendanceSession.objects.filter(employee=employee).exists()


def test_decision_rejects_other_employee_and_conflicting_session(
    field_setup, organization, employee, office
):
    actor, day = field_setup
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)
    with pytest.raises(NotFound):
        FieldWorkService().decide(row.id, telegram_user_id=11111111, decision="CONFIRM")
    open_session(organization, employee, office, started=timezone.now())
    with pytest.raises(Conflict) as error:
        FieldWorkService().decide(row.id, telegram_user_id=777_000_111, decision="CONFIRM")
    assert error.value.details["reason"] == "attendance_conflict"
    row.refresh_from_db()
    assert row.status == "PENDING"


def test_cancel_revokes_credit_with_audit(field_setup, employee):
    actor, day = field_setup
    service = FieldWorkService()
    row = service.create(actor, employee_id=employee.id, day=day, manager_confirmed=True)
    service.decide(row.id, telegram_user_id=777_000_111, decision="CONFIRM")
    service.cancel(actor, row.id)
    daily = AttendanceHrService().daily(actor, employee.id, first=day, last=day)["days"][0]
    assert daily["state"] != "FIELD_WORK" and daily["seconds"] == 0
    assert FieldWorkRequest.objects.get(id=row.id).status == "CANCELLED"
    from humotech.audit.models import AuditLog
    assert AuditLog.objects.filter(action="field_work.cancelled", entity_id=row.id).exists()


def test_scope_and_manager_attestation_required(field_setup, employee, organization,
                                                other_organization, make_actor, nobody_actor):
    actor, day = field_setup
    with pytest.raises(PermissionDenied):
        FieldWorkService().create(nobody_actor, employee_id=employee.id, day=day,
                                  manager_confirmed=True)
    foreign_actor = make_actor(other_organization, permissions=(
        "attendance.read", "attendance.correct"))
    with pytest.raises(NotFound):
        FieldWorkService().create(foreign_actor, employee_id=employee.id, day=day,
                                  manager_confirmed=True)
    with pytest.raises(ValidationFailed):
        FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                  manager_confirmed=False)
    assert not FieldWorkRequest.objects.exists()


def test_expired_request_is_not_sent_or_confirmed(field_setup, employee):
    actor, day = field_setup
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)
    FieldWorkRequest.objects.filter(id=row.id).update(expires_at=timezone.now() - timedelta(seconds=1))
    from humotech.notifications.outbox import claim
    assert not any(item.notification_type == "attendance.field_work_request" for item in claim())
    with pytest.raises(Conflict) as error:
        FieldWorkService().decide(row.id, telegram_user_id=777_000_111, decision="CONFIRM")
    assert error.value.details["reason"] == "closed_or_expired"
    row.refresh_from_db()
    assert row.status == "EXPIRED"


def test_relink_wrong_identity_cannot_respond(field_setup, employee):
    actor, day = field_setup
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)
    from humotech.telegram.models import TelegramAccount
    TelegramAccount.objects.filter(employee=employee).update(
        telegram_user_id=777_000_222)
    with pytest.raises(NotFound):
        FieldWorkService().decide(row.id, telegram_user_id=777_000_111, decision="CONFIRM")
    assert FieldWorkRequest.objects.get(id=row.id).status == "PENDING"


def test_duplicate_request_and_office_scope(field_setup, employee, office,
                                             make_actor, organization, region):
    actor, day = field_setup
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)
    with pytest.raises(Conflict):
        FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                  manager_confirmed=True)
    from django_tests.conftest import make_office
    other_office = make_office(organization, region, "FIELD-OTHER")
    scoped_actor = make_actor(organization, permissions=("attendance.read", "attendance.correct"),
                              office=other_office)
    with pytest.raises(PermissionDenied):
        FieldWorkService().cancel(scoped_actor, row.id)


def test_manual_entry_and_schedule_edit_reject_confirmed_field_work(
    field_setup, employee, office, organization, make_actor
):
    actor, day = field_setup
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)
    FieldWorkService().decide(row.id, telegram_user_id=777_000_111, decision="CONFIRM")
    from humotech.schedules.services import WorkScheduleService
    from humotech.schedules.models import EmployeeScheduleAssignment
    moment = datetime.combine(day, time(9), tzinfo=office_zone(office))
    editor = make_actor(organization, permissions=("attendance.manual", "schedules.manage"))
    with pytest.raises(Conflict) as error:
        AttendanceHrService().manual_event(editor, employee_id=employee.id,
            office_id=office.id, event_type="ENTRY", occurred_at=moment, reason="Поправка")
    assert error.value.details["reason"] == "field_work_conflict"
    schedule = EmployeeScheduleAssignment.objects.get(employee=employee).schedule
    with pytest.raises(Conflict):
        WorkScheduleService().update(editor, schedule.id, weekly_minutes=45 * 60)
    # A name edit cannot change the credited time and remains allowed.
    WorkScheduleService().update(editor, schedule.id, name="Новая подпись")


def test_dashboard_separates_field_work_from_arrivals(field_setup, employee):
    actor, day = field_setup
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)
    from humotech.analytics.dashboard import DashboardService

    def cards():
        return {card.key: card.value for card in DashboardService().summary(actor, day=day).cards}

    pending = cards()
    assert pending["should_work_today"] == 1
    assert pending["field_work_pending"] == 1
    assert pending["field_work"] == 0
    assert pending["came"] == 0

    FieldWorkService().decide(row.id, telegram_user_id=777_000_111, decision="CONFIRM")
    confirmed = cards()
    assert confirmed["should_work_today"] == 1
    assert confirmed["field_work_pending"] == 0
    assert confirmed["field_work"] == 1
    assert confirmed["came"] == 0
