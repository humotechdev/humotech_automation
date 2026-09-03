"""Ограничения, которые обязана держать сама база, а не прикладной код."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from django.db import IntegrityError, connection, transaction

from humotech.employees.models import Employee, EmployeeAssignment
from humotech.offices.models import Office
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.telegram.models import TelegramAccount

pytestmark = pytest.mark.django_db


def test_office_requires_region(db, organization):
    """Офис не существует сам по себе — он всегда внутри региона."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Office.objects.create(
                organization_id=organization.id,
                region_id=None,
                code="NOREGION",
                name="Офис без региона",
                address="—",
                timezone="Asia/Dushanbe",
                status="ACTIVE",
            )


def test_office_region_must_exist(db, organization):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Office.objects.create(
                organization_id=organization.id,
                region_id=uuid.uuid4(),
                code="GHOST",
                name="Офис в несуществующем регионе",
                address="—",
                timezone="Asia/Dushanbe",
                status="ACTIVE",
            )


def test_employee_requires_organization(db):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Employee.objects.create(
                organization_id=None,
                employee_number="EMP-X",
                first_name="Без",
                last_name="Организации",
                hire_date=date(2025, 1, 1),
                employment_status="ACTIVE",
            )


def test_employee_number_unique_within_organization(db, organization, employee):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Employee.objects.create(
                organization_id=organization.id,
                employee_number=employee.employee_number,
                first_name="Пётр",
                last_name="Петров",
                hire_date=date(2025, 3, 1),
                employment_status="ACTIVE",
            )


def test_termination_cannot_precede_hire(db, organization):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Employee.objects.create(
                organization_id=organization.id,
                employee_number="EMP-BAD-DATES",
                first_name="Анна",
                last_name="Сидорова",
                hire_date=date(2025, 6, 1),
                termination_date=date(2025, 1, 1),
                employment_status="TERMINATED",
            )


def test_employee_with_history_cannot_be_deleted(db, organization, office, employee,
                                                 qr_point, now):
    """ON DELETE RESTRICT: история отметок не даёт стереть сотрудника."""
    AttendanceEvent.objects.create(
        organization_id=organization.id,
        employee_id=employee.id,
        office_id=office.id,
        qr_point_id=qr_point.id,
        event_type="ENTRY",
        source="QR",
        verification_status="ACCEPTED",
        occurred_at=now,
        received_at=now,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM employees WHERE id = %s", [employee.id]
                )


def test_one_telegram_account_per_employee(db, organization, employee, now):
    TelegramAccount.objects.create(
        organization_id=organization.id,
        employee_id=employee.id,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        status="ACTIVE",
        connected_at=now,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TelegramAccount.objects.create(
                organization_id=organization.id,
                employee_id=employee.id,
                telegram_user_id=2002,
                telegram_chat_id=2002,
                status="ACTIVE",
                connected_at=now,
            )


def test_one_employee_per_telegram_user(db, organization, office, employee, now):
    second = Employee.objects.create(
        organization_id=organization.id,
        employee_number="EMP-0002",
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2025, 1, 1),
        employment_status="ACTIVE",
    )

    TelegramAccount.objects.create(
        organization_id=organization.id, employee_id=employee.id,
        telegram_user_id=777, telegram_chat_id=777,
        status="ACTIVE", connected_at=now,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TelegramAccount.objects.create(
                organization_id=organization.id, employee_id=second.id,
                telegram_user_id=777, telegram_chat_id=777,
                status="ACTIVE", connected_at=now,
            )


def test_absence_period_cannot_end_before_it_starts(db, organization, employee):
    """end_at < start_at должен отбиваться базой, а не только формой в CRM."""
    from humotech.absences.models import (
        AbsenceRequest,
        AbsenceType,
        EmployeeAbsence,
    )

    absence_type = AbsenceType.objects.create(
        organization_id=organization.id, code="SICK_LEAVE", name="Больничный"
    )
    request = AbsenceRequest.objects.create(
        organization_id=organization.id,
        employee_id=employee.id,
        absence_type_id=absence_type.id,
        request_kind="CREATE",
        status="APPROVED",
    )

    start = datetime(2026, 5, 10, tzinfo=timezone.utc)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            EmployeeAbsence.objects.create(
                organization_id=organization.id,
                employee_id=employee.id,
                absence_type_id=absence_type.id,
                origin_request_id=request.id,
                start_at=start,
                end_at=start - timedelta(days=2),
                status="PLANNED",
            )


def test_two_open_sessions_are_impossible(db, organization, office, employee,
                                          qr_point, now):
    """Частичный уникальный индекс: у сотрудника максимум одна открытая сессия."""
    def make_event() -> AttendanceEvent:
        return AttendanceEvent.objects.create(
            organization_id=organization.id,
            employee_id=employee.id,
            office_id=office.id,
            qr_point_id=qr_point.id,
            event_type="ENTRY",
            source="QR",
            verification_status="ACCEPTED",
            occurred_at=now,
            received_at=now,
        )

    AttendanceSession.objects.create(
        organization_id=organization.id, employee_id=employee.id,
        office_id=office.id, entry_event_id=make_event().id,
        started_at=now, status="OPEN",
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AttendanceSession.objects.create(
                organization_id=organization.id, employee_id=employee.id,
                office_id=office.id, entry_event_id=make_event().id,
                started_at=now + timedelta(minutes=1), status="OPEN",
            )


def test_same_nonce_cannot_be_accepted_twice(db, organization, office, employee,
                                             qr_point, now):
    """Уникальность nonce среди ПРИНЯТЫХ событий."""
    def add_event(status: str, nonce: str) -> None:
        AttendanceEvent.objects.create(
            organization_id=organization.id, employee_id=employee.id,
            office_id=office.id, qr_point_id=qr_point.id,
            event_type="ENTRY", source="QR", verification_status=status,
            occurred_at=now, received_at=now, qr_nonce_hash=nonce,
        )

    add_event("ACCEPTED", "nonce-abc")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            add_event("ACCEPTED", "nonce-abc")


def test_rejected_reuse_of_nonce_is_still_recorded(db, organization, office,
                                                   employee, qr_point, now):
    """Попытка повтора обязана остаться в журнале — иначе обман не расследовать."""
    for status in ("ACCEPTED", "REJECTED", "REJECTED"):
        AttendanceEvent.objects.create(
            organization_id=organization.id, employee_id=employee.id,
            office_id=office.id, qr_point_id=qr_point.id,
            event_type="ENTRY", source="QR", verification_status=status,
            occurred_at=now, received_at=now, qr_nonce_hash="nonce-xyz",
            rejection_reason=None if status == "ACCEPTED" else "NONCE_REUSED",
        )

    stored = AttendanceEvent.objects.filter(qr_nonce_hash="nonce-xyz").count()
    assert stored == 3


def test_qr_event_requires_qr_point(db, organization, office, employee, now):
    """source = QR без QR-точки бессмысленен: офис было бы не из чего вывести."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AttendanceEvent.objects.create(
                organization_id=organization.id, employee_id=employee.id,
                office_id=office.id, qr_point_id=None,
                event_type="ENTRY", source="QR",
                verification_status="ACCEPTED",
                occurred_at=now, received_at=now,
            )


def test_primary_assignments_cannot_overlap(db, organization, office, employee):
    """EXCLUDE USING gist: два пересекающихся основных назначения запрещены."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            EmployeeAssignment.objects.create(
                organization_id=organization.id,
                employee_id=employee.id,
                office_id=office.id,
                employment_type="FULL_TIME",
                work_mode="ONSITE",
                is_primary=True,
                valid_from=date(2025, 1, 1),
            )


def test_role_scope_cannot_be_region_and_office_at_once(db, organization, region,
                                                        office, employee):
    from humotech.accounts.models import UserRoleScope
    from humotech.rbac.models import Role
    from humotech.accounts.models import User

    role = Role.objects.create(
        code="REGIONAL_HR", name="Региональный HR", is_system=True
    )
    user = User(
        organization_id=organization.id,
        employee_id=employee.id,
        email="hr@humotech.tj",
        status="ACTIVE",
    )
    user.set_password("не важно")
    user.save()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            UserRoleScope.objects.create(
                organization_id=organization.id,
                user_id=user.id,
                role_id=role.id,
                region_id=region.id,
                office_id=office.id,
            )
