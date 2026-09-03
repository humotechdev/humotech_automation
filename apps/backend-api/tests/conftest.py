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
from datetime import date, datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.modules.employees.models import Employee, EmployeeAssignment
from src.modules.offices.models import Office
from src.modules.organizations.models import Organization
from src.modules.qr_codes.models import OfficeQrPoint
from src.modules.regions.models import Region

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
