"""Настройки для разработки. Секретный ключ здесь фиктивный и годится
только локально: в production он обязателен из окружения."""

from config.settings.base import *  # noqa: F401,F403
from config.settings.base import env, env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-not-a-real-secret-key-change-me")
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
