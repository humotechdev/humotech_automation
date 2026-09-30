"""Регрессии проверки безопасности 30.09.2026.

Две проблемы, воспроизведённые на изолированном стенде настоящими
HTTP-запросами и исправленные:

1. Фильтр `?region_id=` расширял область до всего региона тому, кому был
   виден лишь один офис в нём (горизонтальное повышение прав).
2. Кадровик с карточкой сотрудника оформлял присутствие сам себе —
   ручную отметку, исправление и выездной день (нет разделения
   обязанностей, в отличие от модуля отсутствий).
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest

from django_tests.conftest import create_actor, make_office
from humotech.accounts.models import UserRoleScope
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceEvent
from humotech.core.errors import PermissionDenied
from humotech.core.rbac import AccessControl
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.rbac.models import Permission, Role, RolePermission

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


# --- аудит-журнал ограничен областью ответственности --------------------------


def test_office_scoped_auditor_does_not_see_org_wide_audit(
    api_client, make_user, make_actor, organization, office
):
    """Аудитор с одним офисом не видит журнал всей организации.

    Раньше `audit.read` + организация отдавали ВСЕ записи, включая
    `old/new_values` сотрудников чужих офисов."""
    from humotech.regions.services import RegionService

    org_wide = make_actor(organization, permissions=("regions.manage",))
    region = RegionService().create(org_wide, code="AUD-ORG", name="Орг",
                                    timezone="Asia/Dushanbe")

    scoped = make_user(organization, permissions=("audit.read",), office=office)
    api_client.force_authenticate(user=scoped)
    rows = api_client.get("/api/v1/audit-logs", {"entity_type": "regions"}).json()["items"]
    assert not any(r["entity_id"] == str(region.id) for r in rows), \
        "office-scoped auditor must not see org-wide audit rows"

    # Org-wide аудитор ту же запись видит.
    wide = make_user(organization, permissions=("audit.read",))
    api_client.force_authenticate(user=wide)
    rows = api_client.get("/api/v1/audit-logs", {"entity_id": str(region.id)}).json()["items"]
    assert any(r["entity_id"] == str(region.id) for r in rows)


# --- Django Admin: только неограниченный SUPER_ADMIN --------------------------


def test_scoped_super_admin_is_not_django_staff(
    make_user, organization, other_organization, office
):
    """SUPER_ADMIN, назначенный на один офис, не входит в Django Admin.

    Только выданный на всю организацию (без сужения) SUPER_ADMIN получает
    признак Django-суперпользователя; иначе назначение на офис открыло бы
    админку, которая данные по области не фильтрует."""
    scoped = make_user(organization, role_code="SUPER_ADMIN", office=office)
    assert scoped.is_staff is False
    assert scoped.is_superuser is False

    # Второй SUPER_ADMIN той же организации не завести (уникальный код
    # роли), поэтому неограниченный берём в отдельной организации.
    unscoped = make_user(other_organization, role_code="SUPER_ADMIN")
    assert unscoped.is_staff is True
    assert unscoped.is_superuser is True


# --- §3: право из одного назначения не действует на территории другого --------
#
# Комбинация назначений давала повышение прав: право «оформить отметку» из
# назначения на офис A действовало и в офисе B, выданном отдельным назначением
# лишь на чтение (union разрешений × union территорий). `scope_for` берёт
# территории только тех назначений, чья роль несёт нужное право. Законные
# широкие роли (директор на всю организацию, региональный HR) при этом НЕ
# сужаются — на это положительные проверки ниже.


def _add_grant(organization, user, *, permissions, role_code,
               office=None, region=None):
    role = Role.objects.create(
        organization=organization, code=role_code, name=role_code
    )
    for code in permissions:
        perm, _ = Permission.objects.get_or_create(
            code=code, defaults={"name": code, "description": code}
        )
        RolePermission.objects.create(role=role, permission=perm)
    UserRoleScope.objects.create(
        organization=organization, user=user, role=role,
        office=office, region=region,
        valid_from=datetime.now(tz=dt_timezone.utc) - timedelta(days=1),
    )


def _two_grant_actor(organization, region):
    """A — право на запись; B — только чтение. Возвращает (actor, A, B)."""
    office_a = make_office(organization, region, "U3-A")
    office_b = make_office(organization, region, "U3-B")
    user, actor = create_actor(
        organization,
        permissions=("attendance.manual", "attendance.read"),
        office=office_a, role_code="U3_WRITE_A",
    )
    _add_grant(organization, user, permissions=("attendance.read",),
               office=office_b, role_code="U3_READ_B")
    return actor, office_a, office_b


_AT = datetime(2026, 9, 15, 4, 0, tzinfo=dt_timezone.utc)


def test_scope_for_confines_permission_to_its_own_grant(organization, region):
    actor, office_a, office_b = _two_grant_actor(organization, region)
    access = AccessControl()
    # Общая область — оба офиса: просмотр B тоже расширяет область.
    assert {office_a.id, office_b.id} <= access.scope(actor).office_ids
    # Область права на запись — только офис A.
    manual = access.scope_for(actor, "attendance.manual").office_ids
    assert office_a.id in manual
    assert office_b.id not in manual


def test_manual_attendance_confined_to_write_grant_office(organization, region):
    actor, office_a, office_b = _two_grant_actor(organization, region)
    emp_a = _employee(organization, office_a, "U3-EA")
    emp_b = _employee(organization, office_b, "U3-EB")
    svc = AttendanceHrService()
    # B — только чтение: ручная отметка запрещена.
    with pytest.raises(PermissionDenied):
        svc.manual_event(actor, employee_id=emp_b.id, office_id=office_b.id,
                         event_type="ENTRY", occurred_at=_AT, reason="x")
    # A — право есть: отметка проходит. Контроль, что чиним не «запретить всё».
    event = svc.manual_event(actor, employee_id=emp_a.id, office_id=office_a.id,
                             event_type="ENTRY", occurred_at=_AT, reason="x")
    assert event.office_id == office_a.id


def test_org_wide_grant_still_writes_in_any_office(organization, region):
    """Директор на всю организацию пишет в любом офисе — роль не сужена."""
    office = make_office(organization, region, "U3-ORG")
    emp = _employee(organization, office, "U3-ORG1")
    actor = create_actor(organization, permissions=("attendance.manual",))[1]
    event = AttendanceHrService().manual_event(
        actor, employee_id=emp.id, office_id=office.id,
        event_type="ENTRY", occurred_at=_AT, reason="x")
    assert event.office_id == office.id


def test_regional_grant_still_writes_in_every_office_of_region(organization, region):
    """Региональная роль пишет в любом офисе своего региона."""
    office = make_office(organization, region, "U3-REG")
    emp = _employee(organization, office, "U3-REG1")
    actor = create_actor(organization, permissions=("attendance.manual",),
                         region=region)[1]
    event = AttendanceHrService().manual_event(
        actor, employee_id=emp.id, office_id=office.id,
        event_type="ENTRY", occurred_at=_AT, reason="x")
    assert event.office_id == office.id


def test_roles_manage_scope_confined_to_its_grant(organization, region):
    """Право выдавать роли из назначения на офис A не действует в офисе B.

    Это опаснейшая комбинация: раздача ролей в B открыла бы путь к любым
    правам там. `_require_within_own_scope` теперь берёт область в разрезе
    `roles.manage`, а не общую."""
    office_a = make_office(organization, region, "U3-RA")
    office_b = make_office(organization, region, "U3-RB")
    user, actor = create_actor(organization, permissions=("roles.manage",),
                               office=office_a, role_code="U3_ROLES_A")
    _add_grant(organization, user, permissions=("employees.read",),
               office=office_b, role_code="U3_VIEW_B")
    access = AccessControl()
    assert office_b.id in access.scope(actor).office_ids  # общая область
    manage = access.scope_for(actor, "roles.manage").office_ids
    assert office_a.id in manage
    assert office_b.id not in manage
