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


# --- запрет настоящих сетевых вызовов ------------------------------------


def _allowed_addresses() -> set[str]:
    """Адреса, к которым тестам ходить можно: только своя база.

    Больше ничего наружу в тестах не открывается. Список собирается по
    фактической настройке подключения, а не по имени хоста в строке:
    в docker база — это `postgres`, на голой машине — `127.0.0.1`, и
    сравнение по строке сломалось бы на первом же переезде.
    """
    import socket as _socket

    from django.conf import settings as _settings

    allowed = {"127.0.0.1", "::1"}
    host = (_settings.DATABASES["default"].get("HOST") or "").strip()
    if host:
        try:
            for family, _, _, _, address in _socket.getaddrinfo(host, None):
                if family in (_socket.AF_INET, _socket.AF_INET6):
                    allowed.add(address[0])
        except OSError:  # pragma: no cover — база всё равно не поднимется
            pass
    return allowed


class RealNetworkCallBlocked(RuntimeError):
    """Тест попытался выйти в настоящую сеть."""


@pytest.fixture(scope="session", autouse=True)
def no_real_network():
    """Любое обращение наружу проваливает тест немедленно.

    История вопроса простая: проверка интерфейса уведомлений однажды
    закончилась настоящими сообщениями живым людям. Заглушка отправщика
    защищает ровно до тех пор, пока её не забыли подставить, а этот
    предохранитель не зависит от аккуратности вызывающего: он ловит сам
    системный вызов.

    Разрешена только база. Telegram, OpenAI и любой другой внешний адрес
    дают исключение с именем адреса — не молчаливый таймаут, по которому
    потом гадают.
    """
    import socket

    allowed = _allowed_addresses()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _check(address) -> None:
        if not isinstance(address, tuple) or not address:
            return  # AF_UNIX и прочее без IP — не выход наружу
        host = str(address[0])
        if host in allowed:
            return
        raise RealNetworkCallBlocked(
            f"Тест попытался открыть соединение с {host}. "
            "Наружу из тестов ходить нельзя: внешние отправители "
            "подменяются заглушкой."
        )

    def guarded_connect(self, address, *args, **kwargs):
        _check(address)
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        _check(address)
        return real_connect_ex(self, address, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex


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


# --- фикстуры AI-модуля ---
#
# Сеть здесь не блокируется на уровне сокетов, как раньше: провайдеры
# подставляются дублёрами явно, а настройки ниже держат модуль включённым
# с заведомо нерабочим ключом — настоящего обращения наружу произойти
# не может, потому что настоящий провайдер не создаётся.

from humotech.ai_assistant.config import AiSettings
from humotech.ai_assistant.services.scoping import EmployeeScope


@pytest.fixture()
def ai_settings_fixture() -> AiSettings:
    """Настройки для тестов: модуль включён, ключ фиктивный."""
    return AiSettings(
        ai_assistant_enabled=True,
        ai_fallback_enabled=True,
        openai_api_key="test-key-not-real",
        openai_chat_model="test-chat-model",
        openai_fallback_model="test-fallback-model",
        openai_embedding_model="test-embedding-model",
        ai_exact_faq_threshold=0.92,
        ai_rag_min_score=0.72,
        ai_conflict_score_delta=0.05,
        ai_max_retrieved_chunks=6,
        ai_query_max_length=1000,
        ai_cache_ttl_seconds=300,
        ai_prompt_version="v1",
        ai_rate_limit_per_minute=100,
        ai_rate_limit_per_day=1000,
        ai_supported_languages="ru,en",
        ai_default_language="ru",
    )


# Имя `settings` занято pytest-django (там это настройки Django),
# поэтому фикстура называется иначе, а здесь заведён псевдоним для
# перенесённых тестов.
@pytest.fixture()
def ai_settings(ai_settings_fixture) -> AiSettings:
    return ai_settings_fixture


@pytest.fixture()
def scope() -> EmployeeScope:
    """Область сотрудника без обращения к базе: у чисто логических тестов
    её незачем строить настоящими записями."""
    return EmployeeScope(
        employee_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        office_id=uuid.uuid4(),
        region_id=uuid.uuid4(),
        department_id=None,
        language="ru",
        employment_status="ACTIVE",
    )


@pytest.fixture()
def fresh_qr(now: datetime) -> dict:
    """Параметры действующего меняющегося QR: выпущен только что, живёт 45 секунд."""
    return {
        "qr_issued_at": now,
        "qr_expires_at": now + timedelta(seconds=45),
    }


@pytest.fixture()
def other_qr_point(db, organization, other_office) -> OfficeQrPoint:
    return make_qr_point(organization, other_office, code="BRANCH_ENTRANCE")


# --- привязка Telegram ---
#
# Ограничение частоты в DRF считает в кэше процесса, а кэш живёт дольше
# одного теста. Без сброса порядок тестов начинает влиять на результат:
# тест, который просто вызывает endpoint, падает с 429 из-за соседа.

@pytest.fixture(autouse=True)
def reset_throttle_counters():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


# Токен заведомо ненастоящий и в Telegram не отправляется: им только
# подписываются строки `initData` внутри тестов.
TEST_BOT_TOKEN = "123456:TEST-BOT-TOKEN-NOT-REAL"
TEST_BOT_SECRET = "test-bot-to-backend-secret"


@pytest.fixture()
def telegram_settings(settings):
    """Настройки Telegram для тестов.

    Отдельная фикстура, а не значения в `config/settings/test.py`: тест,
    проверяющий поведение БЕЗ токена бота, должен уметь его убрать.
    """
    settings.TELEGRAM = {
        **settings.TELEGRAM,
        "BOT_TOKEN": TEST_BOT_TOKEN,
        "BOT_USERNAME": "humotech_test_bot",
        "BOT_API_SECRET": TEST_BOT_SECRET,
        "INIT_DATA_MAX_AGE_SECONDS": 300,
        "INVITATION_TTL_SECONDS": 86400,
        "MINI_APP_SESSION_SECONDS": 43200,
        "MINI_APP_ALLOWED_ORIGINS": ["https://mini.humotech.tj"],
    }
    # CORS читает свой словарь: списки origin'ов у Mini App и у экранов
    # показа QR разные, и держать их в настройках Telegram было бы неверно.
    settings.CORS_ORIGINS = {
        "MINI_APP_ALLOWED_ORIGINS": ["https://mini.humotech.tj"],
        "QR_DISPLAY_ALLOWED_ORIGINS": ["https://qr.humotech.tj"],
    }
    return settings.TELEGRAM


def build_init_data(
    *,
    bot_token: str = TEST_BOT_TOKEN,
    telegram_user_id: int = 777_000_111,
    auth_date: datetime | None = None,
    username: str | None = "ivan",
    extra: dict | None = None,
    tamper: dict | None = None,
) -> str:
    """Строка `initData`, подписанная так же, как её подписывает Telegram.

    `tamper` подменяет поля УЖЕ ПОСЛЕ подписи — ровно то, что сделал бы
    злоумышленник, у которого есть чужая подписанная строка.
    """
    import hashlib
    import hmac
    import json
    from urllib.parse import urlencode

    moment = auth_date or datetime.now(tz=dt_timezone.utc)
    user = {"id": telegram_user_id, "first_name": "Иван", "language_code": "ru"}
    if username:
        user["username"] = username

    fields = {
        "auth_date": str(int(moment.timestamp())),
        "query_id": "AAEtest",
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
    }
    fields.update(extra or {})

    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    fields["hash"] = hmac.new(
        secret, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    fields.update(tamper or {})
    return urlencode(fields)


# --- вход сотрудника ---
#
# Привязка через полный путь HR -> ссылка -> переход -> подтверждение здесь
# не нужна: он проверен отдельно. Тестам сотруднической части нужна уже
# готовая рабочая привязка, и делать её каждый раз в четыре шага значило бы
# проверять чужой сценарий заодно со своим.

def link_telegram(
    employee,
    *,
    telegram_user_id: int = 777_000_111,
    status: str = "ACTIVE",
    username: str | None = "ivan",
):
    """Готовая привязка Telegram нужного состояния."""
    from humotech.telegram.models import TelegramAccount

    return TelegramAccount.objects.create(
        organization=employee.organization,
        employee=employee,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_user_id,
        telegram_username=username,
        language_code="ru",
        status=status,
        connected_at=datetime.now(tz=dt_timezone.utc),
    )


@pytest.fixture()
def linked_account(db, employee, telegram_settings):
    """Сотрудник с подтверждённой привязкой."""
    return link_telegram(employee)


def bot_headers(telegram_user_id: int = 777_000_111, *, secret: str = TEST_BOT_SECRET):
    """Заголовки бота, действующего за сотрудника.

    Ровно два: общий секрет и подтверждённый Telegram ID. Ни `employee_id`,
    ни `organization_id` здесь нет и появиться не может — их негде принять.
    """
    return {
        "HTTP_X_BOT_TOKEN": secret,
        "HTTP_X_TELEGRAM_USER_ID": str(telegram_user_id),
    }


@pytest.fixture()
def bot_client():
    """Отдельный клиент для бота.

    Именно отдельный: общий `api_client` в этом наборе уже носит сессию HR,
    и подмешанная авторизация превратила бы отказ в успех незаметно.
    """
    from rest_framework.test import APIClient

    return APIClient()


def _day_bounds(day, tz_name: str = "Asia/Dushanbe"):
    """Сутки целиком в поясе офиса, приведённые к UTC.

    Не в UTC напрямую: офис живёт в +5, и «сутки» от полуночи UTC
    накрывают пять часов СЛЕДУЮЩЕГО местного дня. Отсутствие, заданное
    так, молча становится двухдневным — и знаменатель посещаемости
    уезжает на день.
    """
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(tz_name)
    start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=zone)
    end = datetime(day.year, day.month, day.day, 23, 59, tzinfo=zone)
    return start.astimezone(dt_timezone.utc), end.astimezone(dt_timezone.utc)


@pytest.fixture()
def make_absence(db, organization):
    """Согласованное отсутствие, накрывающее проверяемый день.

    Границы хранятся моментами времени, а не датами: сутки берутся
    целиком в поясе офиса, иначе перекрытие поехало бы на границе дня.
    """
    from humotech.absences.models import (
        AbsenceRequest,
        AbsenceType,
        EmployeeAbsence,
    )

    def _make(employee, *, code="SICK_LEAVE", day, tz_name="Asia/Dushanbe"):
        first_moment, last_moment = _day_bounds(day, tz_name)
        absence_type, _ = AbsenceType.objects.get_or_create(
            organization=organization,
            code=code,
            defaults={"name": code.title(), "is_paid": True,
                      "requires_approval": True},
        )
        # `origin_request_id` объявлен NOT NULL: отсутствие существует
        # только как следствие согласованной заявки. Отсутствия «просто
        # так», без основания, в схеме нет — и это правильно.
        origin = AbsenceRequest.objects.create(
            organization=organization,
            employee=employee,
            absence_type=absence_type,
            request_kind="CREATE",
            requested_start_at=first_moment,
            requested_end_at=last_moment,
            status="APPROVED",
            submitted_at=first_moment,
        )
        return EmployeeAbsence.objects.create(
            origin_request=origin,
            organization=organization,
            employee=employee,
            absence_type=absence_type,
            start_at=first_moment,
            end_at=last_moment,
            start_date=day,
            end_date=day,
            status="ACTIVE",
        )

    return _make
