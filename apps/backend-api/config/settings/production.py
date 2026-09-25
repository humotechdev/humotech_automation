"""Боевые настройки. Всё чувствительное — только из окружения.

Значений по умолчанию здесь нет намеренно: отсутствующая переменная должна
ронять запуск, а не молча превращаться в небезопасное значение.
"""

from config.settings.base import *  # noqa: F401,F403
from config.settings.base import env

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
# Плавная смена ключа: новый — в DJANGO_SECRET_KEY, прежний — сюда (через
# запятую). Подписи, сделанные прежним ключом (сессии CRM, ссылки сброса),
# продолжают проверяться, пока его не уберут отсюда; новые подписываются
# только новым. Без этого смена ключа разлогинивает всех сразу.
SECRET_KEY_FALLBACKS = [
    k.strip() for k in env("DJANGO_SECRET_KEY_FALLBACKS", "").split(",") if k.strip()
]

# Общий секрет бота — единственное, что отличает запрос бота («этот
# Telegram ID — такой-то сотрудник») от запроса кого угодно. Пустой секрет
# закрывает привязку, но и ломает бота молча; короткий перебирается.
# Поэтому в боевых настройках он обязателен и не короче 32 символов
# (secrets.token_urlsafe(32) даёт 43).
MIN_BOT_API_SECRET_LENGTH = 32
if len(TELEGRAM.get("BOT_API_SECRET") or "") < MIN_BOT_API_SECRET_LENGTH:  # noqa: F405
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "TELEGRAM_BOT_API_SECRET обязателен в боевых настройках и должен быть "
        f"не короче {MIN_BOT_API_SECRET_LENGTH} символов. Сгенерировать: "
        'python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )

# SQL_ECHO в боевых настройках запрещён: журнал запросов Django пишет
# параметры — имена, телефоны, паспортные данные и хеши паролей уходили
# бы в `docker logs`. Переменная здесь молча игнорируется, а не роняет
# запуск: забытый флаг из .env разработчика не должен останавливать стенд.
LOGGING["loggers"]["django.db.backends"]["level"] = "INFO"  # noqa: F405
# Пустые элементы («a.example,,b.example» или хвостовая запятая) отбрасываются:
# пустая строка в ALLOWED_HOSTS ничего не разрешает, но прячет опечатку.
ALLOWED_HOSTS = [
    h.strip() for h in env("DJANGO_ALLOWED_HOSTS", required=True).split(",") if h.strip()
]
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise RuntimeError(
        "DJANGO_ALLOWED_HOSTS: нужен явный список имён. «*» в боевых "
        "настройках отключает проверку Host целиком."
    )

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
# Доверенный origin без TLS в боевых настройках — это разрешение принимать
# изменяющие запросы со страницы, которую подменит любой в той же сети.
_insecure_origins = [o for o in CSRF_TRUSTED_ORIGINS if not o.startswith("https://")]
if _insecure_origins:
    raise RuntimeError(
        "DJANGO_CSRF_TRUSTED_ORIGINS: в боевых настройках допустимы только "
        f"https-адреса, получено: {_insecure_origins!r}"
    )

# Явно, а не умолчанием Django: эти значения — часть договора со шлюзом
# (он не дублирует их на /api/), и смена умолчания в новой версии Django
# не должна менять заголовки молча.
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
