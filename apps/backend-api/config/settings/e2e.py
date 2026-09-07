"""Настройки изолированного стенда для сквозной проверки очереди.

Отдельный модуль, а не «production с другими переменными», ровно по одной
причине: здесь стоит предохранитель, которого в рабочих настройках быть
не должно и не может. Стенд обязан работать на СВОЕЙ базе, и проверяется
это при загрузке настроек — раньше, чем что-либо успеет записать.

Сеть у этого стенда внутренняя (`internal: true` в compose), TLS в ней
нет и прокси нет, поэтому `SECURE_SSL_REDIRECT` не включается: он увёл бы
бота на https-адрес, которого в изолированной сети не существует.

Отправляет здесь заглушка (`NOTIFICATIONS_SENDER=stub` у бота). Настоящий
токен в этот стенд не передаётся, и заглушка отказывается работать, если
он всё-таки появился.
"""

import re

from config.settings.base import *  # noqa: F401,F403
from config.settings.base import DATABASES, env

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY", "e2e-only-not-a-real-secret-key")
ALLOWED_HOSTS = [
    host.strip()
    for host in env("DJANGO_ALLOWED_HOSTS", "backend-e2e,localhost,127.0.0.1").split(",")
    if host.strip()
]

# Имена баз, на которых стенду позволено работать. Список разрешённого:
# неизвестное имя считается рабочим и отвергается.
ALLOWED_DATABASE = re.compile(r"^test_|_e2e$")

_name = DATABASES["default"].get("NAME") or ""
if not ALLOWED_DATABASE.search(_name):
    raise RuntimeError(
        f"config.settings.e2e запущен на базе «{_name}». Изолированный "
        "стенд работает только на базе вида test_* или *_e2e: на рабочей "
        "базе его синтетические уведомления получили бы настоящих "
        "адресатов."
    )

# Быстрый хеш: на стенде проверяют очередь, а не стойкость паролей.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
