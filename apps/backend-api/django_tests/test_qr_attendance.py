"""Поведение серверной обработки QR-скана."""

from __future__ import annotations

from datetime import timedelta

import pytest

from humotech.employees.models import EmployeeOfficeAccess
from humotech.attendance.models import AttendanceSession
from humotech.attendance.services import (
    RejectionReason,
    open_session_for,
    register_scan,
)

pytestmark = pytest.mark.django_db


def test_entry_opens_session(employee, qr_point, now, fresh_qr):
    result = register_scan(employee_id=employee.id, qr_point=qr_point, now=now,
        qr_nonce_hash="n1", **fresh_qr,
    )

    assert result.accepted
    assert result.event.event_type == "ENTRY"
    assert result.event.verification_status == "ACCEPTED"
    # офис взят из QR-точки, а не от клиента
    assert result.event.office_id == qr_point.office_id
    assert result.session is not None
    assert result.session.status == "OPEN"
    assert result.session.ended_at is None


def test_exit_closes_session_and_counts_duration(employee, qr_point, now,
                                                 fresh_qr):
    register_scan(employee_id=employee.id, qr_point=qr_point, now=now,
                  qr_nonce_hash="n1", **fresh_qr)

    later = now + timedelta(hours=8)
    result = register_scan(employee_id=employee.id, qr_point=qr_point, now=later,
        occurred_at=later, qr_nonce_hash="n2",
        qr_issued_at=later, qr_expires_at=later + timedelta(seconds=45),
    )

    assert result.accepted
    assert result.event.event_type == "EXIT"
    assert result.session.status == "CLOSED"
    assert result.session.ended_at == later
    assert result.session.duration_seconds == 8 * 3600
    assert open_session_for(employee_id=employee.id) is None


def test_second_entry_is_rejected_and_does_not_open_second_session(organization, office, employee, now, fresh_qr
):
    """Повторный вход отклоняется — но проверять это нужно на точке ENTRY.

    У точки BOTH второе сканирование вообще не является повторным входом:
    направление выводится из наличия открытой сессии, поэтому там это выход.
    Ситуация «уже внутри» возможна только на точке, жёстко заданной как ENTRY.
    """
    from django_tests.conftest import make_qr_point

    entry_point = make_qr_point(organization, office, code="ENTRY_ONLY", direction_mode="ENTRY"
    )

    register_scan(employee_id=employee.id, qr_point=entry_point, now=now,
                  qr_nonce_hash="n1", **fresh_qr)

    result = register_scan(employee_id=employee.id, qr_point=entry_point,
                           now=now, qr_nonce_hash="n2", **fresh_qr)

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.ALREADY_INSIDE
    assert result.session is None
    open_sessions = AttendanceSession.objects.filter(
        employee_id=employee.id, status="OPEN"
    ).count()
    assert open_sessions == 1


def test_both_mode_second_scan_closes_session_instead_of_opening_new(employee, qr_point, now, fresh_qr
):
    """Регрессия: у точки BOTH второе сканирование закрывает сессию.

    Направление определяет сервер по наличию открытой сессии, а не клиент.
    Второй сессии при этом не появляется.
    """
    first = register_scan(employee_id=employee.id, qr_point=qr_point, now=now,
                          qr_nonce_hash="n1", **fresh_qr)
    assert first.event.event_type == "ENTRY"

    later = now + timedelta(hours=4)
    second = register_scan(employee_id=employee.id, qr_point=qr_point, now=later,
        occurred_at=later, qr_nonce_hash="n2",
        qr_issued_at=later, qr_expires_at=later + timedelta(seconds=45),
    )

    assert second.accepted
    assert second.event.event_type == "EXIT"
    assert second.session.id == first.session.id, "открылась вторая сессия"
    assert second.session.status == "CLOSED"

    total = AttendanceSession.objects.filter(employee_id=employee.id).count()
    assert total == 1


def test_expired_qr_is_rejected(employee, qr_point, now):
    issued = now - timedelta(minutes=2)
    result = register_scan(employee_id=employee.id, qr_point=qr_point, now=now,
        qr_nonce_hash="stale",
        qr_issued_at=issued,
        qr_expires_at=issued + timedelta(seconds=45),
    )

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.QR_EXPIRED
    # отклонённая попытка записана в журнал...
    assert result.event.verification_status == "REJECTED"
    assert result.event.id is not None
    # ...но рабочую сессию не создала
    assert result.session is None
    assert open_session_for(employee_id=employee.id) is None


def test_rejected_scan_never_creates_session(employee, other_qr_point, now,
                                             fresh_qr):
    result = register_scan(employee_id=employee.id, qr_point=other_qr_point,
                           now=now, qr_nonce_hash="n1", **fresh_qr)

    assert not result.accepted
    assert AttendanceSession.objects.count() == 0


def test_nonce_cannot_be_reused_by_same_employee(employee, qr_point, now,
                                                 fresh_qr):
    first = register_scan(employee_id=employee.id, qr_point=qr_point,
                          now=now, qr_nonce_hash="same-nonce", **fresh_qr)
    assert first.accepted

    # выходим, чтобы вторая попытка не отбилась просто как ALREADY_INSIDE
    later = now + timedelta(hours=1)
    register_scan(employee_id=employee.id, qr_point=qr_point, now=later,
                  occurred_at=later, qr_nonce_hash="exit-nonce",
                  qr_issued_at=later, qr_expires_at=later + timedelta(seconds=45))

    replay = register_scan(employee_id=employee.id, qr_point=qr_point,
        now=later + timedelta(minutes=1),
        occurred_at=later + timedelta(minutes=1),
        qr_nonce_hash="same-nonce",
        qr_issued_at=later,
        qr_expires_at=later + timedelta(hours=2),
    )

    assert not replay.accepted
    assert replay.rejection_reason == RejectionReason.NONCE_REUSED
    assert replay.session is None


def test_scan_in_foreign_office_is_rejected(employee, other_qr_point, now,
                                            fresh_qr):
    """Сотрудник не может отметиться в чужом офисе без выданного доступа."""
    result = register_scan(employee_id=employee.id, qr_point=other_qr_point,
                           now=now, qr_nonce_hash="foreign", **fresh_qr)

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.OFFICE_NOT_ALLOWED


def test_granted_office_access_allows_scan(organization, employee,
                                           other_office, other_qr_point, now,
                                           fresh_qr):
    EmployeeOfficeAccess.objects.create(
        organization_id=organization.id,
        employee_id=employee.id,
        office_id=other_office.id,
        access_type="TEMPORARY",
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
    )


    result = register_scan(employee_id=employee.id, qr_point=other_qr_point,
                           now=now, qr_nonce_hash="foreign-ok", **fresh_qr)

    assert result.accepted
    assert result.event.office_id == other_office.id


def test_expired_office_access_does_not_allow_scan(organization, employee,
                                                   other_office, other_qr_point,
                                                   now, fresh_qr):
    EmployeeOfficeAccess.objects.create(
        organization_id=organization.id,
        employee_id=employee.id,
        office_id=other_office.id,
        access_type="TEMPORARY",
        valid_from=now - timedelta(days=10),
        valid_to=now - timedelta(days=1),
    )

    result = register_scan(employee_id=employee.id, qr_point=other_qr_point,
                           now=now, qr_nonce_hash="expired-access", **fresh_qr)

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.OFFICE_NOT_ALLOWED


def test_client_time_far_from_server_time_is_rejected(employee, qr_point,
                                                      now, fresh_qr):
    result = register_scan(employee_id=employee.id, qr_point=qr_point, now=now,
        occurred_at=now - timedelta(hours=3),
        qr_nonce_hash="drift", **fresh_qr,
    )

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.CLOCK_DRIFT


def test_geolocation_required_but_missing(organization, office, employee,
                                          now, fresh_qr):
    from django_tests.conftest import make_qr_point

    point = make_qr_point(organization, office, code="GEO_GATE", require_geolocation=True
    )
    result = register_scan(employee_id=employee.id, qr_point=point, now=now,
                           qr_nonce_hash="geo", **fresh_qr)

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.GEOLOCATION_REQUIRED


def test_exit_only_point_rejects_when_not_inside(organization, office,
                                                 employee, now, fresh_qr):
    from django_tests.conftest import make_qr_point

    exit_point = make_qr_point(organization, office, code="EXIT_GATE", direction_mode="EXIT"
    )
    result = register_scan(employee_id=employee.id, qr_point=exit_point,
                           now=now, qr_nonce_hash="exit-first", **fresh_qr)

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.NOT_INSIDE


def test_inactive_qr_point_is_rejected(organization, office, employee, now,
                                       fresh_qr):
    from django_tests.conftest import make_qr_point

    point = make_qr_point(organization, office, code="OFF_GATE",
                          is_active=False)
    result = register_scan(employee_id=employee.id, qr_point=point, now=now,
                           qr_nonce_hash="inactive", **fresh_qr)

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.QR_POINT_INACTIVE
