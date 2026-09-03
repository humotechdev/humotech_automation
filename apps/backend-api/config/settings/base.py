"""Общие настройки HUMOTECH Automation.

Всё, что зависит от окружения, читается из переменных окружения и `.env`.
Секретов в коде нет и быть не может: файл лежит в Git.

Настройки разделены на development / test / production. Общий модуль не должен
знать, где он выполняется, — иначе одна забытая проверка `DEBUG` превращается
в боевую конфигурацию с отладкой.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Минимальное чтение .env без лишней зависимости.

    Переменная, уже заданная в окружении, приоритетнее файла: так запуск
    в контейнере и в CI не перебивается локальным файлом разработчика.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"Переменная окружения {name} обязательна и не задана. "
            f"См. {BASE_DIR / '.env.example'}"
        )
    return value or ""


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def database_from_url(url: str) -> dict:
    """URL PostgreSQL -> словарь DATABASES для Django.

    Отдельная функция, а не dj-database-url: одна зависимость меньше, а разбор
    URL здесь занимает десять строк. Диалект SQLAlchemy (`postgresql+psycopg`)
    тоже принимается — в проекте остались строки подключения в таком виде.
    """
    parsed = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    if parsed.scheme not in ("postgresql", "postgres"):
        raise RuntimeError(
            f"Поддерживается только PostgreSQL, получено: {parsed.scheme!r}. "
            "Схема опирается на pgvector, EXCLUDE USING gist, JSONB, CIDR "
            "и частичные индексы — SQLite ничего из этого не умеет."
        )
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "127.0.0.1",
        "PORT": str(parsed.port or 5432),
        "ATOMIC_REQUESTS": False,
        "CONN_MAX_AGE": int(env("DJANGO_CONN_MAX_AGE", "0")),
    }


# --- база ---
#
# Django работает со своей базой. Старая база Alembic (`humotech`) не трогается:
# на время перехода это две независимые схемы.
DATABASES = {
    "default": database_from_url(
        env(
            "DJANGO_DATABASE_URL",
            "postgresql://humotech:humotech_local@127.0.0.1:5433/humotech_django",
        )
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Две проверки Django отключены осознанно, обе — с разбором в
# docs/architecture/django-migration-audit.md.
#
# models.E034 — «имя индекса не длиннее 30 символов». Это ограничение
#   идентификаторов Oracle, а не PostgreSQL: в PostgreSQL предел 63 байта.
#   Проект работает только с PostgreSQL, и `database_from_url` выше отвергает
#   любой другой движок. 58 из 96 индексов уже существующей схемы имеют имена
#   длиннее 30 символов; сокращать их значило бы менять схему ради проверки
#   на несовместимость с базой, которая здесь не используется.
#   Самое длинное имя в схеме — 49 символов.
#
# auth.E003 — «поле USERNAME_FIELD обязано быть уникальным». Почта уникальна
#   ВНУТРИ организации и без учёта регистра (`uq_users_org_lower_email`),
#   глобальной уникальности нет намеренно: один адрес может принадлежать
#   разным людям в разных организациях. Вход выполняет свой backend,
#   который различает пользователей по паре «организация + почта».
# models.W045 — «CHECK с RawSQL не проверяется в full_clean()». Так и задумано:
#   эти ограничения существуют только в базе, и проверять их дважды не нужно.
#   Текст выражения задан явно ради точного совпадения с уже развёрнутой схемой
#   (Django строит из Q(...) NULL-безопасную форму с другим текстом).
#   Прикладные проверки живут в слое сервисов и дают понятные сообщения,
#   а база остаётся последним рубежом.
#
# auth.W004 — то же самое, что auth.E003, но в виде предупреждения:
#   «убедитесь, что backend справляется с неуникальными именами».
#   Справляется — ровно для этого `OrganizationEmailBackend` и написан.
SILENCED_SYSTEM_CHECKS = [
    "models.E034", "auth.E003", "auth.W004", "models.W045",
]

# Учётная запись CRM — своя: в схеме уже есть `users` со своими колонками,
# и подменять её моделью django.contrib.auth нельзя.
AUTH_USER_MODEL = "accounts.User"

# Вход по паре «организация + почта»: почта уникальна внутри организации,
# а не глобально, поэтому стандартный ModelBackend здесь не подходит.
AUTHENTICATION_BACKENDS = ["humotech.accounts.backends.OrganizationEmailBackend"]

DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "django_filters",
    "pgvector.django",
]

# Приложения по доменам: одно к одному с прежними модулями backend-api,
# чтобы перенос был переносом, а не переделкой.
HUMOTECH_APPS = [
    # ядро идёт первым: его миграция ставит расширения PostgreSQL
    "humotech.core",
    "humotech.organizations",
    "humotech.regions",
    "humotech.offices",
    "humotech.departments",
    "humotech.positions",
    "humotech.rbac",
    "humotech.accounts",
    "humotech.employees",
    "humotech.schedules",
    "humotech.qr_codes",
    "humotech.devices",
    "humotech.attendance",
    "humotech.absences",
    "humotech.files",
    "humotech.telegram",
    "humotech.knowledge",
    "humotech.ai_assistant",
    "humotech.questions",
    "humotech.notifications",
    "humotech.audit",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + HUMOTECH_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- локаль и время ---
#
# Всё хранится в UTC, часовой пояс — свойство офиса, а не сервера.
# Ровно тот же принцип, что был в схеме: TIMESTAMPTZ в UTC.
LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- DRF ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    # Обработчик приводит доменные ошибки к тому же телу ответа,
    # что и раньше: {"error": {"code", "message", "details"}}.
    "EXCEPTION_HANDLER": "humotech.core.exceptions.domain_exception_handler",
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {
            "level": "DEBUG" if env_bool("SQL_ECHO") else "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}
