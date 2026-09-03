"""Общие фикстуры тестов.

Тестам нужен НАСТОЯЩИЙ PostgreSQL: схема опирается на CIDR, INET, JSONB,
частичные индексы, `gen_random_uuid()` и EXCLUDE ... USING gist. SQLite ничего
из этого не умеет, поэтому подмена базы здесь была бы самообманом.

Адрес базы берётся из TEST_DATABASE_URL, например:

    TEST_DATABASE_URL=postgresql+psycopg://postgres:PASS@127.0.0.1:5432/humotech_test

Схема разворачивается НАСТОЯЩЕЙ миграцией Alembic — так тесты заодно проверяют,
что миграция применима, а не только что модели описаны.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from src.core.permissions.catalog import ALL_PERMISSION_CODES
from src.core.rbac import Actor
from src.modules.employees.models import Employee, EmployeeAssignment
from src.modules.offices.models import Office
from src.modules.organizations.models import Organization
from src.modules.qr_codes.models import OfficeQrPoint
from src.modules.regions.models import Region
from src.modules.roles.models import Permission, Role, RolePermission, UserRoleScope
from src.modules.schedules.models import ScheduleDay, WorkSchedule
from src.modules.users.models import User

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _test_database_url() -> str | None:
    return os.getenv("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def engine():
    url = _test_database_url()
    if not url:
        pytest.skip(
            "Не задан TEST_DATABASE_URL. Пример:\n"
            "  TEST_DATABASE_URL=postgresql+psycopg://postgres:PASS@127.0.0.1:5432/humotech_test"
        )
    # предохранитель: тесты стирают схему целиком, поэтому база обязана быть тестовой
    db_name = url.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in db_name.lower():
        pytest.fail(
            f"Имя базы '{db_name}' не похоже на тестовое. "
            "Тесты пересоздают схему public — укажите отдельную базу с 'test' в имени."
        )

    eng = create_engine(url, future=True)
    with eng.begin() as conn:
        # Сносим только таблицы, а не схему целиком.
        #
        # DROP SCHEMA public CASCADE удалил бы вместе со схемой и расширение
        # btree_gist, а его пересоздание требует прав суперпользователя — то есть
        # тестовая роль обязана была бы быть суперпользователем на каждом прогоне.
        # Так расширение ставится один раз при заведении базы, а дальше
        # CREATE EXTENSION IF NOT EXISTS в миграции превращается в no-op.
        conn.execute(
            text(
                """
                DO $$
                DECLARE r record;
                BEGIN
                    FOR r IN (
                        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
                    ) LOOP
                        EXECUTE 'DROP TABLE IF EXISTS public.'
                                || quote_ident(r.tablename) || ' CASCADE';
                    END LOOP;
                END $$;
                """
            )
        )

    cfg = Config(os.path.join(APP_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(APP_DIR, "migrations"))
    os.environ["ALEMBIC_DATABASE_URL"] = url
    command.upgrade(cfg, "head")

    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine) -> Session:
    """Каждый тест выполняется в своей транзакции и откатывается после."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# --- базовые сущности ---

@pytest.fixture()
def organization(db: Session) -> Organization:
    org = Organization(
        code=f"ORG{uuid.uuid4().hex[:8]}",
        name="HUMOTECH",
        default_timezone="Asia/Dushanbe",
        status="ACTIVE",
    )
    db.add(org)
    db.flush()
    return org


@pytest.fixture()
def region(db: Session, organization: Organization) -> Region:
    reg = Region(
        organization_id=organization.id,
        code="DUSHANBE",
        name="Душанбе",
        status="ACTIVE",
    )
    db.add(reg)
    db.flush()
    return reg


@pytest.fixture()
def other_region(db: Session, organization: Organization) -> Region:
    reg = Region(
        organization_id=organization.id,
        code="KHUJAND",
        name="Худжанд",
        status="ACTIVE",
    )
    db.add(reg)
    db.flush()
    return reg


def _make_office(db: Session, organization, region, code: str) -> Office:
    office = Office(
        organization_id=organization.id,
        region_id=region.id,
        code=code,
        name=f"Офис {code}",
        address="ул. Рудаки, 1",
        timezone="Asia/Dushanbe",
        status="ACTIVE",
    )
    db.add(office)
    db.flush()
    return office


@pytest.fixture()
def office(db: Session, organization: Organization, region: Region) -> Office:
    return _make_office(db, organization, region, "MAIN")


@pytest.fixture()
def other_office(db: Session, organization: Organization, other_region: Region) -> Office:
    return _make_office(db, organization, other_region, "BRANCH")


@pytest.fixture()
def employee(db: Session, organization: Organization, office: Office) -> Employee:
    emp = Employee(
        organization_id=organization.id,
        employee_number="EMP-0001",
        first_name="Иван",
        last_name="Иванов",
        hire_date=date(2024, 2, 1),
        employment_status="ACTIVE",
    )
    db.add(emp)
    db.flush()
    db.add(
        EmployeeAssignment(
            organization_id=organization.id,
            employee_id=emp.id,
            office_id=office.id,
            employment_type="FULL_TIME",
            work_mode="ONSITE",
            is_primary=True,
            valid_from=date(2024, 2, 1),
        )
    )
    db.flush()
    return emp


def make_qr_point(
    db: Session,
    organization: Organization,
    office: Office,
    *,
    code: str = "MAIN_ENTRANCE",
    direction_mode: str = "BOTH",
    **kwargs,
) -> OfficeQrPoint:
    point = OfficeQrPoint(
        organization_id=organization.id,
        office_id=office.id,
        code=code,
        name="Главный вход",
        direction_mode=direction_mode,
        qr_mode="ROTATING",
        rotation_seconds=45,
        **kwargs,
    )
    db.add(point)
    db.flush()
    return point


@pytest.fixture()
def qr_point(db: Session, organization: Organization, office: Office) -> OfficeQrPoint:
    return make_qr_point(db, organization, office)


@pytest.fixture()
def other_qr_point(
    db: Session, organization: Organization, other_office: Office
) -> OfficeQrPoint:
    return make_qr_point(db, organization, other_office, code="BRANCH_ENTRANCE")


@pytest.fixture()
def now() -> datetime:
    return datetime.now(tz=timezone.utc)


@pytest.fixture()
def fresh_qr(now: datetime):
    """Параметры действующего rotating QR: выпущен только что, живёт 45 секунд."""
    return {
        "qr_issued_at": now,
        "qr_expires_at": now + timedelta(seconds=45),
    }


# --- фикстуры HR CRM: пользователи, роли и области видимости ---
#
# Разрешения создаются по требованию, а не сидируются целиком. Так тест,
# проверяющий отказ, отличается от теста, проверяющего разрешение, ровно одним
# списком кодов — и «отказано» не может случиться просто потому, что таблица
# `permissions` осталась пустой.

def _make_user(db: Session, organization: Organization, email: str) -> User:
    user = User(
        organization_id=organization.id,
        email=email,
        password_hash="argon2:test-stub",
        status="ACTIVE",
    )
    db.add(user)
    db.flush()
    return user


def _permission_rows(db: Session, codes: tuple[str, ...]) -> list[Permission]:
    unknown = [c for c in codes if c not in ALL_PERMISSION_CODES]
    if unknown:
        raise AssertionError(
            f"В каталоге нет таких разрешений: {unknown}. "
            "Проверьте src/core/permissions/catalog.py"
        )
    rows = []
    for code in codes:
        row = db.scalar(select(Permission).where(Permission.code == code))
        if row is None:
            row = Permission(code=code, name=code, description=code)
            db.add(row)
            db.flush()
        rows.append(row)
    return rows


@pytest.fixture()
def make_actor(db: Session):
    """Пользователь CRM с заданным набором разрешений и областью видимости.

    `region=None, office=None` означает доступ ко всей организации — ровно так
    же, как это задано в `user_role_scopes`.
    """
    counter = {"n": 0}

    def _make(
        organization: Organization,
        *,
        permissions: tuple[str, ...] = (),
        region=None,
        office=None,
        role_code: str | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> Actor:
        counter["n"] += 1
        suffix = f"{counter['n']}-{uuid.uuid4().hex[:6]}"
        user = _make_user(db, organization, f"actor-{suffix}@humotech.tj")
        role = Role(
            organization_id=organization.id,
            code=role_code or f"TEST_ROLE_{suffix.upper()}",
            name="Тестовая роль",
        )
        db.add(role)
        db.flush()
        for permission in _permission_rows(db, tuple(permissions)):
            db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        db.add(
            UserRoleScope(
                organization_id=organization.id,
                user_id=user.id,
                role_id=role.id,
                region_id=region.id if region is not None else None,
                office_id=office.id if office is not None else None,
                valid_from=valid_from
                or datetime.now(tz=timezone.utc) - timedelta(days=1),
                valid_to=valid_to,
            )
        )
        db.flush()
        return Actor(user_id=user.id, organization_id=organization.id)

    return _make


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
def hr_actor(make_actor, organization: Organization) -> Actor:
    """HR-администратор с доступом ко всей организации."""
    return make_actor(organization, permissions=HR_FULL_PERMISSIONS)


@pytest.fixture()
def readonly_actor(make_actor, organization: Organization) -> Actor:
    """Только чтение: ни одного `.manage`."""
    return make_actor(organization, permissions=HR_READONLY_PERMISSIONS)


@pytest.fixture()
def nobody_actor(make_actor, organization: Organization) -> Actor:
    """Учётная запись без единого разрешения."""
    return make_actor(organization, permissions=())


# --- вторая организация: соседи, данные которых видеть нельзя ---

@pytest.fixture()
def other_organization(db: Session) -> Organization:
    org = Organization(
        code=f"OTHER{uuid.uuid4().hex[:8]}",
        name="Соседняя компания",
        default_timezone="Asia/Tashkent",
        status="ACTIVE",
    )
    db.add(org)
    db.flush()
    return org


@pytest.fixture()
def foreign_region(db: Session, other_organization: Organization) -> Region:
    region = Region(
        organization_id=other_organization.id,
        code="TASHKENT",
        name="Ташкент",
        status="ACTIVE",
    )
    db.add(region)
    db.flush()
    return region


@pytest.fixture()
def foreign_office(
    db: Session, other_organization: Organization, foreign_region: Region
) -> Office:
    return _make_office(db, other_organization, foreign_region, "FOREIGN")


@pytest.fixture()
def foreign_actor(make_actor, other_organization: Organization) -> Actor:
    """Полноправный HR, но в другой организации."""
    return make_actor(other_organization, permissions=HR_FULL_PERMISSIONS)


# --- графики работы ---

def make_work_schedule(
    db: Session,
    organization: Organization,
    *,
    name: str = "Стандартный 09:00-18:00",
    timezone_name: str = "Asia/Dushanbe",
    status: str = "ACTIVE",
) -> WorkSchedule:
    schedule = WorkSchedule(
        organization_id=organization.id,
        name=name,
        timezone=timezone_name,
        weekly_minutes=2400,
        status=status,
    )
    db.add(schedule)
    db.flush()
    for weekday in range(1, 6):
        db.add(
            ScheduleDay(
                schedule_id=schedule.id,
                weekday=weekday,
                is_working_day=True,
                start_time=time(9, 0),
                end_time=time(18, 0),
            )
        )
    db.flush()
    return schedule


@pytest.fixture()
def work_schedule(db: Session, organization: Organization) -> WorkSchedule:
    return make_work_schedule(db, organization)


@pytest.fixture()
def foreign_schedule(db: Session, other_organization: Organization) -> WorkSchedule:
    return make_work_schedule(
        db, other_organization, name="Чужой график", timezone_name="Asia/Tashkent"
    )
