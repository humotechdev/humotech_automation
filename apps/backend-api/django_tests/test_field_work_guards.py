"""Выездная работа: всё, что могло бы засчитать тот же день второй раз.

Каждый путь, создающий присутствие или меняющий норму дня, обязан
отказать, пока на дне висит активный (ждущий ответа или подтверждённый)
запрос выездной работы. Здесь — пути, которых нет в test_field_work.py:
QR, исправление сессии, календарное исключение и ответ бота по HTTP.
"""

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from django_tests.conftest import bot_headers
from django_tests.test_field_work import field_setup  # noqa: F401 — фикстура
from humotech.attendance.field_work import FieldWorkService
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import (
    AttendanceCorrectionRequest,
    AttendanceEvent,
    AttendanceSession,
    FieldWorkRequest,
)
from humotech.attendance.scanning import _REJECTION_TO_STATUS, ScanStatus
from humotech.attendance.services import RejectionReason, register_scan
from humotech.core.errors import Conflict
from humotech.core.timeframes import office_zone
from humotech.schedules.calendar import CalendarExceptionService
from humotech.schedules.models import CalendarException

pytestmark = pytest.mark.django_db

TELEGRAM_ID = 777_000_111


def _confirmed(actor, employee, day):
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)
    return FieldWorkService().decide(row.id, telegram_user_id=TELEGRAM_ID, decision="CONFIRM")


# --- QR ------------------------------------------------------------------------


def test_qr_on_confirmed_day_is_rejected_with_its_own_reason(
    field_setup, employee, qr_point  # noqa: F811
):
    actor, day = field_setup
    _confirmed(actor, employee, day)
    moment = timezone.now()

    result = register_scan(employee_id=employee.id, qr_point=qr_point, now=moment,
                           qr_nonce_hash="fw-1", qr_issued_at=moment,
                           qr_expires_at=moment + timedelta(seconds=45))

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.FIELD_WORK_CONFIRMED
    assert not AttendanceSession.objects.filter(employee=employee).exists()
    # Раньше причины не было в таблице, и сотрудник видел «код не распознан».
    assert _REJECTION_TO_STATUS[result.rejection_reason] == ScanStatus.FIELD_WORK_CONFIRMED


# --- исправление сессии ----------------------------------------------------------


def _session_on(organization, employee, office, day):
    zone = office_zone(office)
    started = datetime.combine(day, time(9), tzinfo=zone)
    ended = datetime.combine(day, time(18), tzinfo=zone)
    events = [
        AttendanceEvent.objects.create(
            organization=organization, employee=employee, office=office,
            event_type=kind, occurred_at=at, received_at=at, source="MANUAL",
            verification_status="ACCEPTED",
        )
        for kind, at in (("ENTRY", started), ("EXIT", ended))
    ]
    return AttendanceSession.objects.create(
        organization=organization, employee=employee, office=office,
        entry_event=events[0], exit_event=events[1], started_at=started,
        ended_at=ended, duration_seconds=9 * 3600, status="CLOSED",
    )


def test_correction_cannot_move_a_session_onto_field_work_day(
    field_setup, organization, employee, office, make_actor  # noqa: F811
):
    actor, day = field_setup
    previous = day - timedelta(days=1)
    session = _session_on(organization, employee, office, previous)
    _confirmed(actor, employee, day)
    zone = office_zone(office)
    request = AttendanceCorrectionRequest.objects.create(
        organization=organization, employee=employee, attendance_session=session,
        requested_entry_at=datetime.combine(day, time(9), tzinfo=zone),
        requested_exit_at=datetime.combine(day, time(18), tzinfo=zone),
        reason="Перепутал день", status="SUBMITTED", submitted_at=timezone.now(),
    )
    reviewer = make_actor(organization, permissions=("attendance.read", "attendance.correct"))

    with pytest.raises(Conflict) as error:
        AttendanceHrService().review_correction(reviewer, request.id, decision="approve")

    assert error.value.details["reason"] == "field_work_conflict"
    session.refresh_from_db()
    request.refresh_from_db()
    assert session.started_at.astimezone(zone).date() == previous
    assert request.status == "SUBMITTED"
    # Отказ по той же заявке по-прежнему возможен: он ничего не засчитывает.
    AttendanceHrService().review_correction(reviewer, request.id, decision="reject")


def test_correction_on_another_day_is_still_approved(
    field_setup, organization, employee, office, make_actor  # noqa: F811
):
    actor, day = field_setup
    previous = day - timedelta(days=1)
    session = _session_on(organization, employee, office, previous)
    _confirmed(actor, employee, day)
    request = AttendanceCorrectionRequest.objects.create(
        organization=organization, employee=employee, attendance_session=session,
        requested_entry_at=datetime.combine(previous, time(8), tzinfo=office_zone(office)),
        reason="Пришёл раньше", status="SUBMITTED", submitted_at=timezone.now(),
    )
    reviewer = make_actor(organization, permissions=("attendance.read", "attendance.correct"))

    AttendanceHrService().review_correction(reviewer, request.id, decision="approve")

    session.refresh_from_db()
    assert session.status == "CORRECTED"


# --- календарь -----------------------------------------------------------------------


@pytest.fixture()
def calendar_actor(organization, make_actor):
    return make_actor(organization, permissions=("schedules.read", "calendar.manage"))


@pytest.mark.parametrize("status", ["PENDING", "CONFIRMED"])
def test_calendar_exception_cannot_land_on_active_field_work(
    field_setup, employee, office, calendar_actor, status  # noqa: F811
):
    actor, day = field_setup
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)
    if status == "CONFIRMED":
        FieldWorkService().decide(row.id, telegram_user_id=TELEGRAM_ID, decision="CONFIRM")
    service = CalendarExceptionService()

    for office_id in (office.id, None):  # свой офис и вся организация
        with pytest.raises(Conflict) as error:
            service.create(calendar_actor, name="Праздник", exception_type="HOLIDAY",
                           date_from=day, office_id=office_id)
        assert error.value.details["reason"] == "field_work_conflict"
    assert not CalendarException.objects.exists()

    # Соседний день свободен.
    service.create(calendar_actor, name="Праздник", exception_type="HOLIDAY",
                   date_from=day + timedelta(days=1), office_id=office.id)

    # После отмены запроса исключение заводится.
    FieldWorkService().cancel(actor, row.id)
    service.create(calendar_actor, name="Праздник", exception_type="HOLIDAY",
                   date_from=day, office_id=office.id)


def test_calendar_exception_elsewhere_is_not_blocked(
    field_setup, organization, region, employee, calendar_actor  # noqa: F811
):
    actor, day = field_setup
    _confirmed(actor, employee, day)
    from django_tests.conftest import make_office
    other = make_office(organization, region, "FW-CAL-OTHER")

    created = CalendarExceptionService().create(
        calendar_actor, name="Закрыт", exception_type="CLOSURE",
        date_from=day, office_id=other.id)

    assert len(created) == 1


def test_calendar_type_change_and_reactivation_are_guarded(
    field_setup, employee, office, calendar_actor  # noqa: F811
):
    actor, day = field_setup
    service = CalendarExceptionService()
    [exception] = service.create(calendar_actor, name="Сокращённый",
                                 exception_type="SHORT_DAY", date_from=day,
                                 office_id=office.id)
    service.deactivate(calendar_actor, exception.id)
    _confirmed(actor, employee, day)

    with pytest.raises(Conflict):
        service.reactivate(calendar_actor, exception.id)
    with pytest.raises(Conflict):
        service.update(calendar_actor, exception.id, exception_type="HOLIDAY")
    # Подпись дня не меняет расчёт и остаётся доступной.
    service.update(calendar_actor, exception.id, name="Новая подпись")

    exception.refresh_from_db()
    assert exception.is_active is False
    assert exception.exception_type == "SHORT_DAY"


# --- ответ бота по HTTP ----------------------------------------------------------------


def _decision_url(row):
    return f"/api/v1/telegram/bot/field-work/{row.id}/decision"


def test_bot_http_decision_by_foreign_telegram_id_is_not_found(
    field_setup, employee, bot_client, other_organization  # noqa: F811
):
    actor, day = field_setup
    from django_tests.conftest import link_telegram
    from humotech.employees.models import Employee
    foreign = Employee.objects.create(
        organization=other_organization, employee_number="EMP-FOREIGN",
        first_name="Чужой", last_name="Сотрудник", hire_date=day - timedelta(days=30),
        employment_status="ACTIVE",
    )
    link_telegram(foreign, telegram_user_id=777_000_999)
    row = FieldWorkService().create(actor, employee_id=employee.id, day=day,
                                    manager_confirmed=True)

    for foreign in (777_000_999, 123_456):  # сотрудник другой организации; никто
        answer = bot_client.post(
            _decision_url(row),
            {"telegram_user_id": foreign, "decision": "CONFIRM"},
            format="json", **bot_headers(foreign),
        )
        assert answer.status_code == 404, answer.content

    no_secret = bot_client.post(
        _decision_url(row), {"telegram_user_id": TELEGRAM_ID, "decision": "CONFIRM"},
        format="json", **bot_headers(TELEGRAM_ID, secret="wrong"),
    )
    assert no_secret.status_code in (401, 403)
    assert FieldWorkRequest.objects.get(id=row.id).status == "PENDING"

    own = bot_client.post(
        _decision_url(row), {"telegram_user_id": TELEGRAM_ID, "decision": "DECLINE"},
        format="json", **bot_headers(TELEGRAM_ID),
    )
    assert own.status_code == 200, own.content
    assert own.json()["status"] == "DECLINED"
    # Повторное нажатие другой кнопки не переписывает решение.
    again = bot_client.post(
        _decision_url(row), {"telegram_user_id": TELEGRAM_ID, "decision": "CONFIRM"},
        format="json", **bot_headers(TELEGRAM_ID),
    )
    assert again.status_code == 409
    assert FieldWorkRequest.objects.get(id=row.id).status == "DECLINED"
