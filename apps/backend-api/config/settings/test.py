"""Настройки тестов.

База — настоящий PostgreSQL с pgvector: схема опирается на EXCLUDE USING gist,
частичные индексы, JSONB, CIDR и vector(1536). На SQLite такие тесты были бы
самообманом, поэтому подмена движка здесь не предусмотрена вовсе.
"""

from config.settings.base import *  # noqa: F401,F403
from config.settings.base import DATABASES, env

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY", "test-only-not-a-real-secret-key")
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Django сам создаёт базу с префиксом test_. Расширения ставят миграции,
# поэтому отдельная подготовка базы не нужна.
DATABASES["default"]["TEST"] = {"NAME": "test_humotech_django"}

# Пароли в тестах не проверяются на стойкость и хешируются быстрым алгоритмом:
# argon2 на каждую фикстуру — это секунды на ровном месте.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
