"""Общие фикстуры Django-тестов.

Тестам нужен НАСТОЯЩИЙ PostgreSQL с pgvector: схема опирается на
EXCLUDE USING gist, частичные индексы, JSONB, CIDR и vector(1536).
SQLite ничего из этого не умеет, поэтому подмена движка здесь была бы
самообманом — и именно это проверяет `postgres_only`.

Данные фикстур перенесены из прежнего `tests/conftest.py` один в один,
включая имена: так перенесённые тесты читаются рядом со старыми.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone as dt_timezone

import pytest
from django.db import connection

from humotech.accounts.models import User, UserRoleScope
from humotech.core.permissions_catalog import ALL_PERMISSION_CODES
from humotech.core.rbac import Actor
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.offices.models import Office
from humotech.organizations.models import Organization
from humotech.qr_codes.models import OfficeQrPoint
from humotech.rbac.models import Permission, Role, RolePermission
from humotech.regions.models import Region
from humotech.schedules.models import ScheduleDay, WorkSchedule

REQUIRED_EXTENSIONS = ("btree_gist", "vector")


@pytest.fixture(scope="session", autouse=True)
def postgres_only(django_db_setup, django_db_blocker):
    """Предохранитель: тесты не должны молча выполниться на другой базе.

    Без этой проверки достаточно одной правки настроек, чтобы весь набор
    прошёл на SQLite — зелёный и ничего не проверяющий.
    """
    assert connection.vendor == "postgresql", (
        f"Тесты требуют PostgreSQL, а настроен {connection.vendor}. "
        "Схема опирается на возможности, которых нет в других базах."
    )
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension")
        installed = {row[0] for row in cursor.fetchall()}
    missing = [name for name in REQUIRED_EXTENSIONS if name not in installed]
    assert not missing, f"В тестовой базе нет расширений: {missing}"


# --- базовые сущности ---

@pytest.fixture()
def organization(db) -> Organization:
    return Organization.objects.create(
        code=f"ORG{uuid.uuid4().hex[:8]}",
        name="HUMOTECH",
        default_timezone="Asia/Dushanbe",
        status="ACTIVE",
    )


@pytest.fixture()
def region(db, organization) -> Region:
    return Region.objects.create(
        organization=organization, code="DUSHANBE", name="Душанбе", status="ACTIVE"
    )


@pytest.fixture()
def other_region(db, organization) -> Region:
    return Region.objects.create(
        organization=organization, code="KHUJAND", name="Худжанд", status="ACTIVE"
    )


def make_office(organization, region, code: str) -> Office:
    return Office.objects.create(
        organization=organization,
        region=region,
        code=code,
        name=f"Офис {code}",
        address="ул. Рудаки, 1",
        timezone="Asia/Dushanbe",
        status="ACTIVE",
    )


@pytest.fixture()
def office(db, organization, region) -> Office:
    return make_office(organization, region, "MAIN")


@pytest.fixture()
def other_office(db, organization, other_region) -> Office:
    return make_office(organization, other_region, "BRANCH")


@pytest.fixture()
def employee(db, organization, office) -> Employee:
    emp = Employee.objects.create(
        organization=organization,
        employee_number="EMP-0001",
        first_name="Иван",
        last_name="Иванов",
        hire_date=date(2024, 2, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization,
        employee=emp,
        office=office,
        employment_type="FULL_TIME",
        work_mode="ONSITE",
        is_primary=True,
        valid_from=date(2024, 2, 1),
    )
    return emp


def make_qr_point(organization, office, *, code="MAIN_ENTRANCE",
                  direction_mode="BOTH", **kwargs) -> OfficeQrPoint:
    return OfficeQrPoint.objects.create(
        organization=organization,
        office=office,
        code=code,
        name="Главный вход",
        direction_mode=direction_mode,
        qr_mode="ROTATING",
        rotation_seconds=45,
        **kwargs,
    )


@pytest.fixture()
def qr_point(db, organization, office) -> OfficeQrPoint:
    return make_qr_point(organization, office)


@pytest.fixture()
def now() -> datetime:
    return datetime.now(tz=dt_timezone.utc)


# --- пользователи, роли и области видимости ---
#
# Разрешения создаются по требованию, а не сидируются целиком. Так тест,
# проверяющий отказ, отличается от теста, проверяющего разрешение, ровно одним
# списком кодов — и «отказано» не может случиться просто потому, что таблица
# разрешений осталась пустой.

def _permission_rows(codes: tuple[str, ...]) -> list[Permission]:
    unknown = [c for c in codes if c not in ALL_PERMISSION_CODES]
    if unknown:
        raise AssertionError(
            f"В каталоге нет таких разрешений: {unknown}. "
            "Проверьте humotech/core/permissions_catalog.py"
        )
    rows = []
    for code in codes:
        row, _ = Permission.objects.get_or_create(
            code=code, defaults={"name": code, "description": code}
        )
        rows.append(row)
    return rows


_COUNTER = {"n": 0}


def create_actor(organization, *, permissions=(), region=None, office=None,
                 role_code=None, valid_from=None, valid_to=None,
                 raw_password: str | None = None) -> tuple[User, Actor]:
    """Учётная запись с ролью и областью видимости.

    Возвращает и пользователя, и `Actor`: сервисам нужен второй, а входу
    по HTTP — первый, и заводить их двумя разными путями значило бы
    проверять не то, что работает в бою.
    """
    _COUNTER["n"] += 1
    suffix = f"{_COUNTER['n']}-{uuid.uuid4().hex[:6]}"
    user = User(
        organization=organization,
        email=f"actor-{suffix}@humotech.tj",
        status="ACTIVE",
    )
    # Пароль всегда через set_password: в базе только хеш, даже в тестах.
    user.set_password(raw_password or uuid.uuid4().hex)
    user.save()

    role = Role.objects.create(
        organization=organization,
        code=role_code or f"TEST_ROLE_{suffix.upper()}",
        name="Тестовая роль",
    )
    for permission in _permission_rows(tuple(permissions)):
        RolePermission.objects.create(role=role, permission=permission)
    UserRoleScope.objects.create(
        organization=organization,
        user=user,
        role=role,
        region=region,
        office=office,
        valid_from=valid_from
        or datetime.now(tz=dt_timezone.utc) - timedelta(days=1),
        valid_to=valid_to,
    )
    return user, Actor(user_id=user.id, organization_id=organization.id)


@pytest.fixture()
def make_actor(db):
    """Пользователь CRM с заданным набором разрешений и областью видимости.

    `region=None, office=None` означает доступ ко всей организации — ровно так
    же, как это задано в `user_role_scopes`.
    """

    def _make(organization, **kwargs) -> Actor:
        return create_actor(organization, **kwargs)[1]

    return _make


@pytest.fixture()
def make_user(db):
    """То же самое, но возвращает учётную запись — для входа по HTTP."""

    def _make(organization, **kwargs) -> User:
        return create_actor(organization, **kwargs)[0]

    return _make


@pytest.fixture()
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


HR_FULL_PERMISSIONS = (
    "regions.read", "regions.manage",
    "offices.read", "offices.manage",
    "departments.manage", "positions.manage",
    "employees.read", "employees.manage", "employees.archive", "employees.access",
    "schedules.read", "schedules.manage",
    "audit.read",
)

HR_READONLY_PERMISSIONS = (
    "regions.read", "offices.read", "employees.read", "schedules.read",
)


@pytest.fixture()
def hr_actor(make_actor, organization) -> Actor:
    """HR-администратор с доступом ко всей организации."""
    return make_actor(organization, permissions=HR_FULL_PERMISSIONS)


@pytest.fixture()
def readonly_actor(make_actor, organization) -> Actor:
    """Только чтение: ни одного `.manage`."""
    return make_actor(organization, permissions=HR_READONLY_PERMISSIONS)


@pytest.fixture()
def nobody_actor(make_actor, organization) -> Actor:
    """Учётная запись без единого разрешения."""
    return make_actor(organization, permissions=())


# --- вторая организация: соседи, данные которых видеть нельзя ---

@pytest.fixture()
def other_organization(db) -> Organization:
    return Organization.objects.create(
        code=f"OTHER{uuid.uuid4().hex[:8]}",
        name="Соседняя компания",
        default_timezone="Asia/Tashkent",
        status="ACTIVE",
    )


@pytest.fixture()
def foreign_region(db, other_organization) -> Region:
    return Region.objects.create(
        organization=other_organization, code="TASHKENT", name="Ташкент",
        status="ACTIVE",
    )


@pytest.fixture()
def foreign_office(db, other_organization, foreign_region) -> Office:
    return make_office(other_organization, foreign_region, "FOREIGN")


@pytest.fixture()
def foreign_actor(make_actor, other_organization) -> Actor:
    """Полноправный HR, но в другой организации."""
    return make_actor(other_organization, permissions=HR_FULL_PERMISSIONS)


# --- графики работы ---

def make_work_schedule(organization, *, name="Стандартный 09:00-18:00",
                       timezone_name="Asia/Dushanbe",
                       status="ACTIVE") -> WorkSchedule:
    schedule = WorkSchedule.objects.create(
        organization=organization,
        name=name,
        timezone=timezone_name,
        weekly_minutes=2400,
        status=status,
    )
    for weekday in range(1, 6):
        ScheduleDay.objects.create(
            schedule=schedule,
            weekday=weekday,
            is_working_day=True,
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
    return schedule


@pytest.fixture()
def work_schedule(db, organization) -> WorkSchedule:
    return make_work_schedule(organization)


@pytest.fixture()
def foreign_schedule(db, other_organization) -> WorkSchedule:
    return make_work_schedule(
        other_organization, name="Чужой график", timezone_name="Asia/Tashkent"
    )
