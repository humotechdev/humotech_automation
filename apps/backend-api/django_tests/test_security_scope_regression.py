"""Регрессии проверки безопасности 30.09.2026.

Две проблемы, воспроизведённые на изолированном стенде настоящими
HTTP-запросами и исправленные:

1. Фильтр `?region_id=` расширял область до всего региона тому, кому был
   виден лишь один офис в нём (горизонтальное повышение прав).
2. Кадровик с карточкой сотрудника оформлял присутствие сам себе —
   ручную отметку, исправление и выездной день (нет разделения
   обязанностей, в отличие от модуля отсутствий).
"""

from datetime import date, datetime, timezone as dt_timezone

import pytest

from django_tests.conftest import create_actor, make_office
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceEvent
from humotech.core.errors import PermissionDenied
from humotech.employees.models import Employee, EmployeeAssignment

pytestmark = pytest.mark.django_db


def _employee(organization, office, number):
    emp = Employee.objects.create(
        organization=organization, employee_number=number,
        first_name="Тест", last_name="Скоуп",
        hire_date=date(2024, 1, 1), employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=emp, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 1, 1),
    )
    return emp


def _event(organization, employee, office):
    return AttendanceEvent.objects.create(
        organization=organization, employee=employee, office=office,
        event_type="ENTRY",
        occurred_at=datetime(2026, 9, 15, 4, 0, tzinfo=dt_timezone.utc),
        received_at=datetime(2026, 9, 15, 4, 0, tzinfo=dt_timezone.utc),
        source="MANUAL", verification_status="ACCEPTED",
    )


def test_region_filter_does_not_widen_office_scope(organization, region):
    """Админ офиса A видит регион как подпись, но не соседний офис B в нём."""
    office_a = make_office(organization, region, "SCOPE-A")
    office_b = make_office(organization, region, "SCOPE-B")
    emp_a = _employee(organization, office_a, "SC-A1")
    emp_b = _employee(organization, office_b, "SC-B1")
    _event(organization, emp_a, office_a)
    _event(organization, emp_b, office_b)

    actor = create_actor(organization, permissions=("attendance.read",), office=office_a)[1]

    # Без фильтра виден только свой офис.
    plain = AttendanceHrService().events(actor)
    assert {row.office_id for row in plain.items} == {office_a.id}

    # С ?region_id= того же региона — по-прежнему только свой офис,
    # а НЕ весь регион (раньше сюда попадал office_b).
    scoped = AttendanceHrService().events(actor, region_id=region.id)
    seen = {row.office_id for row in scoped.items}
    assert office_b.id not in seen, "regional filter widened scope to a foreign office"
    assert seen == {office_a.id}


def test_region_filter_whole_region_for_org_wide_actor(organization, region):
    """Кому виден весь регион (вся организация) — тот видит оба офиса."""
    office_a = make_office(organization, region, "SCOPE-A2")
    office_b = make_office(organization, region, "SCOPE-B2")
    _event(organization, _employee(organization, office_a, "SC-A2"), office_a)
    _event(organization, _employee(organization, office_b, "SC-B2"), office_b)

    actor = create_actor(organization, permissions=("attendance.read",))[1]  # вся организация
    scoped = AttendanceHrService().events(actor, region_id=region.id)
    assert {row.office_id for row in scoped.items} == {office_a.id, office_b.id}


def test_manual_event_forbidden_for_self(organization, office):
    """Кадровик с карточкой сотрудника не ставит отметку сам себе."""
    emp = _employee(organization, office, "SC-SELF")
    user, actor = create_actor(
        organization, permissions=("attendance.manual",), office=office)
    user.employee = emp
    user.save(update_fields=["employee"])

    with pytest.raises(PermissionDenied) as error:
        AttendanceHrService().manual_event(
            actor, employee_id=emp.id, office_id=office.id, event_type="ENTRY",
            occurred_at=datetime(2026, 9, 15, 9, 0, tzinfo=dt_timezone.utc),
            reason="сам себе",
        )
    assert error.value.details.get("reason") == "self_attendance"

    # Чужому сотруднику отметка по-прежнему ставится.
    other = _employee(organization, office, "SC-OTHER")
    AttendanceHrService().manual_event(
        actor, employee_id=other.id, office_id=office.id, event_type="ENTRY",
        occurred_at=datetime(2026, 9, 15, 9, 0, tzinfo=dt_timezone.utc),
        reason="коллеге",
    )
