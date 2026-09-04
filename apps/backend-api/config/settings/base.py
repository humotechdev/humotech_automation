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
    # Схема OpenAPI. Сама по себе ничего не меняет в поведении API:
    # генерируется по маршрутам и сериализаторам при обращении к /api/schema.
    "drf_spectacular",
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
    "humotech.reports",
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
    # CORS только для Mini App и только для его путей — см. модуль.
    "humotech.core.cors.ScopedCorsMiddleware",
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
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Частота обращений к чувствительным endpoint'ам. Считает LocMemCache,
    # то есть на процесс: при нескольких рабочих процессах фактический предел
    # умножается на их число. Это защита от перебора, а не от нагрузки;
    # общий счётчик появится вместе с Redis.
    "DEFAULT_THROTTLE_RATES": {
        # Обмен initData на внутренний токен: сюда приходит каждый запуск
        # Mini App, поэтому предел щедрый, но конечный.
        "telegram_mini_app": env("THROTTLE_TELEGRAM_MINI_APP", "30/min"),
        # Погашение ссылки привязки. Проверка секрета бота идёт РАНЬШЕ
        # ограничения, поэтому чужие запросы счётчик не тратят: предел
        # рассчитан на массовый выход сотрудников по ссылкам в один день.
        "telegram_bot_link": env("THROTTLE_TELEGRAM_BOT_LINK", "60/min"),
        # Выдача ссылок HR. Массовая рассылка ссылок — не рабочий сценарий.
        "telegram_invitations": env("THROTTLE_TELEGRAM_INVITATIONS", "60/min"),
        # Личный кабинет. Считается на сотрудника, а не на адрес: за одним
        # офисным IP сидит весь офис, и общий счётчик закрыл бы доступ всем
        # из-за одного. Предел щедрый — экран обновляет статус сам.
        "employee_self": env("THROTTLE_EMPLOYEE_SELF", "120/min"),
        # Сопряжение экрана: перебор одноразового кода. Предел жёсткий —
        # сопрягают экран один раз, руками, и повторять некому.
        # Отметки. Строже общего предела кабинета: человек отмечается
        # несколько раз в день, а не сто, и под общим пределом перебор
        # кодов прошёл бы незамеченным.
        "employee_scan": env("THROTTLE_EMPLOYEE_SCAN", "20/min"),
        "qr_display_pair": env("THROTTLE_QR_DISPLAY_PAIR", "10/min"),
        # Выдача кодов экрану. Экран обновляет код раз в полминуты; предел
        # рассчитан на то, что после обрыва связи он попробует чаще.
        "qr_display_code": env("THROTTLE_QR_DISPLAY_CODE", "60/min"),
    },
}

def _origin_list(name: str) -> list[str]:
    """Список origin'ов из переменной окружения через запятую.

    Хвостовой слэш снимается: браузер шлёт Origin без него, а человек
    в `.env` его нередко дописывает, и сравнение молча переставало
    совпадать.
    """
    return [
        origin.strip().rstrip("/")
        for origin in env(name, "").split(",")
        if origin.strip()
    ]


# --- Telegram ---
#
# Токен бота живёт ТОЛЬКО здесь, на сервере. Им подписывается `initData`
# Mini App, и любой, у кого он есть, может выпустить строку от имени любого
# сотрудника. Во frontend он не передаётся ни при каких условиях.
TELEGRAM = {
    "BOT_TOKEN": env("TELEGRAM_BOT_TOKEN"),
    "BOT_USERNAME": env("TELEGRAM_BOT_USERNAME"),
    "MINI_APP_URL": env("TELEGRAM_MINI_APP_URL"),
    # Пять минут — рекомендация Telegram. Строка живёт от открытия Mini App
    # до обмена на внутренний токен, дольше ей быть незачем.
    "INIT_DATA_MAX_AGE_SECONDS": int(
        env("TELEGRAM_INIT_DATA_MAX_AGE_SECONDS", "300")
    ),
    # Сколько живёт ссылка привязки. Сутки: HR выдаёт её в рабочее время,
    # человек открывает в тот же день или на следующее утро.
    "INVITATION_TTL_SECONDS": int(env("TELEGRAM_INVITATION_TTL_SECONDS", "86400")),
    # Срок внутреннего токена Mini App. Заметно больше `initData`: строка
    # Telegram выдаётся один раз при открытии, и перевыпустить её нельзя —
    # если внутренний токен протухнет раньше, чем человек закроет приложение,
    # войти повторно будет нечем.
    "MINI_APP_SESSION_SECONDS": int(
        env("TELEGRAM_MINI_APP_SESSION_SECONDS", "43200")
    ),
    # Общий секрет между ботом и backend. Нужен потому, что бот сообщает
    # backend идентификатор Telegram-пользователя как факт: подтвердить его
    # своими силами backend не может. Без этого секрета кто угодно привязал
    # бы к найденной ссылке ЧУЖОЙ Telegram.
    "BOT_API_SECRET": env("TELEGRAM_BOT_API_SECRET"),
    # Origin'ы, которым разрешено обращаться к endpoint'ам Mini App.
    # Пусто = браузерных клиентов нет вовсе. Тот же список читает
    # `CORS_ORIGINS` ниже — здесь он остаётся ради обратной совместимости
    # с кодом, который берёт настройки Mini App одним словарём.
    "MINI_APP_ALLOWED_ORIGINS": _origin_list("TELEGRAM_MINI_APP_ALLOWED_ORIGINS"),
}

# --- QR-коды офиса ---
#
# Ключ подписи ОТДЕЛЬНЫЙ от `SECRET_KEY`. Смысл разделения: сменить ключ
# подписи QR можно в любой момент — протухнут коды на экранах, и через
# полминуты появятся новые; смена `SECRET_KEY` разлогинивает всю CRM.
# Держать их одним значением означало бы, что дешёвая операция стоит дорого.
# Во frontend не уходит ни один из них.
QR = {
    "SIGNING_SECRET": env("QR_SIGNING_SECRET"),
    # Сколько живёт код на экране. Тридцать секунд — компромисс: короче
    # неудобно человеку, который достаёт телефон; длиннее — больше окно,
    # в котором сфотографированный код ещё действует.
    "TOKEN_TTL_SECONDS": int(env("QR_TOKEN_TTL_SECONDS", "30")),
    # Расхождение часов экрана и сервера. Без допуска код, выпущенный
    # «в будущем» на секунду, отвергался бы весь свой срок.
    "CLOCK_SKEW_SECONDS": int(env("QR_CLOCK_SKEW_SECONDS", "10")),
    # Сколько живёт credential экрана. Долго: экран висит на стене, и
    # перевыпуск требует человека с доступом к нему.
    "DISPLAY_CREDENTIAL_TTL_SECONDS": int(
        env("QR_DISPLAY_CREDENTIAL_TTL_SECONDS", "2592000")
    ),
    # Сколько живёт одноразовый код сопряжения. Час: его вводят сразу,
    # а не хранят.
    "DISPLAY_PAIRING_TTL_SECONDS": int(
        env("QR_DISPLAY_PAIRING_TTL_SECONDS", "3600")
    ),
}

# --- Очередь уведомлений ---
#
# Брокера нет: очередь живёт в PostgreSQL (humotech/notifications/outbox.py).
NOTIFICATIONS = {
    # Как часто бот спрашивает новые сообщения.
    "POLL_INTERVAL_SECONDS": int(
        env("NOTIFICATIONS_POLL_INTERVAL_SECONDS", "5")
    ),
    "BATCH_SIZE": int(env("NOTIFICATIONS_BATCH_SIZE", "20")),
    # После стольких неудач сообщение помечается FAILED и больше
    # не повторяется. Молча повторять вечно — способ не заметить,
    # что доставка сломана.
    "MAX_ATTEMPTS": int(env("NOTIFICATIONS_MAX_ATTEMPTS", "5")),
    # Пауза между попытками растёт по степеням двойки от базовой.
    "RETRY_BASE_SECONDS": int(env("NOTIFICATIONS_RETRY_BASE_SECONDS", "30")),
    "RETRY_MAX_SECONDS": int(env("NOTIFICATIONS_RETRY_MAX_SECONDS", "3600")),
    # Через сколько строка, зависшая в RUNNING, считается брошенной.
    # Процесс отправщика мог упасть между захватом и результатом.
    "LOCK_TIMEOUT_SECONDS": int(
        env("NOTIFICATIONS_LOCK_TIMEOUT_SECONDS", "300")
    ),
}

# --- Приложенные файлы ---
#
# Справка о болезни — медицинский документ. Каталог намеренно вне `MEDIA_URL`:
# веб-сервер из него ничего не раздаёт, файл отдаёт view, который сначала
# спрашивает, кому можно.
FILES = {
    "PROVIDER": env("FILES_PROVIDER", "local"),
    "PRIVATE_ROOT": env("FILES_PRIVATE_ROOT", str(BASE_DIR / "private-media")),
    # Антивируса в проекте нет. Пока флаг выключен, загруженный файл
    # помечается CLEAN, и это означает «проверка не проводилась», а не
    # «проверен и чист» — см. humotech/files/storage.py. Включение флага
    # переводит новые файлы в PENDING, и до проверки они не отдаются.
    "SCANNER_ENABLED": env("FILES_SCANNER_ENABLED", "false").lower() == "true",
}

# --- Фоновые выгрузки ---
#
# Готовый файл — это кадровые данные, лежащие на диске. Каталог тот же по
# смыслу, что и у справок: вне зоны раздачи статики, веб-сервер из него
# ничего не отдаёт, файл выдаёт view, который сначала спрашивает, кому можно.
EXPORTS = {
    "PRIVATE_ROOT": env("EXPORTS_PRIVATE_ROOT", str(BASE_DIR / "private-exports")),
    # Через сколько часов файл удаляется. Выгрузка персональных данных,
    # лежащая вечно, — это утечка, отложенная во времени.
    "RETENTION_HOURS": int(env("EXPORTS_RETENTION_HOURS", "72")),
    # После стольких неудач задание помечается FAILED и больше не берётся.
    "MAX_ATTEMPTS": int(env("EXPORTS_MAX_ATTEMPTS", "3")),
    "RETRY_BASE_SECONDS": int(env("EXPORTS_RETRY_BASE_SECONDS", "60")),
    # Через сколько секунд задание, зависшее в RUNNING, считается брошенным.
    # Процесс исполнителя мог упасть между захватом и результатом.
    "LOCK_TIMEOUT_SECONDS": int(env("EXPORTS_LOCK_TIMEOUT_SECONDS", "900")),
    # Пауза между опросами очереди, когда работы нет.
    "POLL_INTERVAL_SECONDS": int(env("EXPORTS_POLL_INTERVAL_SECONDS", "5")),
    # Сколько незавершённых заказов может держать один человек. Не
    # техническое ограничение: сто нажатий подряд — это не сто отчётов,
    # это один отчёт и девяносто девять лишних файлов на диске.
    "MAX_PENDING_PER_USER": int(env("EXPORTS_MAX_PENDING_PER_USER", "5")),
}

# --- Доверенные прокси ---
#
# Сколько прокси стоит перед приложением. Ноль означает: `X-Forwarded-For`
# не читается вовсе, адрес берётся из соединения.
#
# Это умолчание намеренно закрытое. Заголовок подделывает кто угодно, и
# приложение, читающее его без настроенного прокси, само сообщает нужный
# адрес — а проверка «внутри офисной сети» держится ровно на нём.
TRUSTED_PROXY_COUNT = int(env("TRUSTED_PROXY_COUNT", "0"))

# --- CORS ---
#
# Ключи соответствуют зонам в `humotech/core/cors.py`. Списки РАЗНЫЕ
# намеренно: адрес Mini App не должен попутно открывать выдачу QR-кодов.
CORS_ORIGINS = {
    "MINI_APP_ALLOWED_ORIGINS": _origin_list("TELEGRAM_MINI_APP_ALLOWED_ORIGINS"),
    "QR_DISPLAY_ALLOWED_ORIGINS": _origin_list("QR_DISPLAY_ALLOWED_ORIGINS"),
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


# --- схема OpenAPI ----------------------------------------------------------
#
# Схема описывает пути, методы, параметры и коды ответов. Тела запросов
# и ответов она берёт из сериализаторов — там, где они есть; у части
# наборов действий ответ собирается вручную (`page_response`), и для них
# точное описание даёт документация для фронтенда:
# docs/api/hr-crm.md. Схема и документ дополняют друг друга, а не
# дублируют: расходиться им негде, потому что путь один и тот же.
SPECTACULAR_SETTINGS = {
    "TITLE": "HUMOTECH HR CRM API",
    "DESCRIPTION": (
        "REST API кадровой системы. Авторизация — сессия Django "
        "(cookie + CSRF), не JWT. Область видимости и права проверяет "
        "сервер по авторизованному пользователю; organization_id от "
        "клиента не принимается ни в одном запросе."
    ),
    "VERSION": "1.0.0",
    # Схема отдаётся отдельным адресом, а не вместе со списком маршрутов.
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAuthenticated"],
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    # Одинаково названные поля с разными наборами значений генератор
    # сводит в один компонент и, не сумев, выдумывает имя вроде
    # `Status650Enum`. Такое имя попадает в сгенерированные типы клиента
    # и меняется от любой правки — поэтому имена задаются здесь.
    "ENUM_NAME_OVERRIDES": {
        "AttendanceEventType": "humotech.core.enums.ATTENDANCE_EVENT_TYPES",
        "ExportKind": "humotech.reports.sheets.EXPORT_KINDS",
        # ACTIVE/INACTIVE/ARCHIVED — один и тот же набор у организации,
        # региона, отдела, должности и графика. Это не совпадение, а одно
        # состояние справочной записи, и в схеме оно должно быть одним
        # компонентом с осмысленным именем.
        "ReferenceStatus": "humotech.core.enums.REGION_STATUSES",
    },
}
