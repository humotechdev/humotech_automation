"""Инфраструктура: адрес клиента за прокси, ограничение частоты, настройки.

Атакующий сценарий, ради которого написан файл: клиент присылает
`X-Forwarded-For: 6.6.6.6` (каждый раз новый). До исправления:

- шлюз nginx ДОПИСЫВАЛ адрес в заголовок (`$proxy_add_x_forwarded_for`),
  и строка клиента доходила до Django первой записью;
- у DRF не было NUM_PROXIES, и SimpleRateThrottle считал частоту по ВСЕЙ
  строке заголовка — новый заголовок на каждый запрос обнулял счётчик.

Теперь шлюз перезаписывает заголовок одним адресом (проверено в
одноразовом контейнере, см. отчёт infra), backend работает с
TRUSTED_PROXY_COUNT=1, и NUM_PROXIES у DRF равен тому же числу.

Проверки конфигов nginx и compose живут вне этого каталога (контейнер
тестов видит только apps/backend-api) и описаны в отчёте.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import RequestFactory
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle

from humotech.core.clientip import client_ip

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _request(xff: str | None = None, remote: str = "172.22.0.9"):
    extra = {"REMOTE_ADDR": remote}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return RequestFactory().get("/api/v1/x", **extra)


# --- client_ip ------------------------------------------------------------


def test_client_ip_ignores_forwarded_for_without_trusted_proxies(settings):
    settings.TRUSTED_PROXY_COUNT = 0
    assert client_ip(_request("6.6.6.6", remote="198.51.100.20")) == "198.51.100.20"


def test_client_ip_takes_single_address_written_by_gateway(settings):
    # Шлюз после исправления: X-Forwarded-For = ровно один адрес клиента.
    settings.TRUSTED_PROXY_COUNT = 1
    assert client_ip(_request("198.51.100.20")) == "198.51.100.20"


def test_client_ip_spoofed_prefix_is_not_trusted(settings):
    # Старый шлюз дописывал: «то, что прислал клиент» + «адрес соединения».
    # Даже в таком виде берётся самая правая запись, а не подделка.
    settings.TRUSTED_PROXY_COUNT = 1
    assert client_ip(_request("6.6.6.6, 198.51.100.20")) == "198.51.100.20"


@pytest.mark.parametrize(
    "xff",
    [
        "",
        " , ,",
        "not-an-ip",
        "198.51.100.20:443",
        "1" * 5000,
        "::ffff:6.6.6.6\x00",
        "６.６.６.６",  # полноширинные цифры
    ],
)
def test_client_ip_garbage_falls_back_to_connection(settings, xff):
    settings.TRUSTED_PROXY_COUNT = 1
    assert client_ip(_request(xff, remote="172.22.0.9")) == "172.22.0.9"


@pytest.mark.parametrize(
    ("topology", "gateway_peer"),
    [
        # Локальный стенд: ngrok → gateway (realip от ngrok) → backend.
        ("local: ngrok -> gateway -> backend", "172.18.0.7"),
        # test-server: Caddy (172.31.250.10) → gateway (realip только от
        # Caddy) → backend. Адрес соединения у backend — шлюз в сети app.
        ("test-server: caddy -> gateway -> backend", "172.19.0.5"),
    ],
)
def test_client_ip_both_deploy_topologies(settings, topology, gateway_peer):
    """В обеих топологиях шлюз ПЕРЕЗАПИСЫВАЕТ X-Forwarded-For одним адресом,
    а compose задаёт TRUSTED_PROXY_COUNT=1. Разные клиенты — разные адреса
    (а не один IP шлюза на всех), подделка слева ничего не меняет."""
    settings.TRUSTED_PROXY_COUNT = 1
    alice = client_ip(_request("198.51.100.20", remote=gateway_peer))
    bob = client_ip(_request("203.0.113.44", remote=gateway_peer))
    assert (alice, bob) == ("198.51.100.20", "203.0.113.44"), topology
    # Без согласованного числа прокси все клиенты сливаются в адрес шлюза —
    # тот самый общий счётчик входа, который один человек выбирает за всех.
    settings.TRUSTED_PROXY_COUNT = 0
    assert client_ip(_request("198.51.100.20", remote=gateway_peer)) == gateway_peer


def test_client_ip_shorter_chain_than_proxies_falls_back(settings):
    settings.TRUSTED_PROXY_COUNT = 2
    assert client_ip(_request("6.6.6.6", remote="172.22.0.9")) == "172.22.0.9"


def test_client_ip_ipv6(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    assert client_ip(_request("2001:db8::1")) == "2001:db8::1"


# --- DRF throttle ident -----------------------------------------------------


class _Throttle(SimpleRateThrottle):
    rate = "5/min"
    scope = "infra-test"

    def get_cache_key(self, request, view):  # pragma: no cover - не нужен
        return None


def _ident(xff, remote="172.22.0.9"):
    return _Throttle().get_ident(_request(xff, remote=remote))


def test_num_proxies_follows_trusted_proxy_count():
    from django.conf import settings

    assert settings.REST_FRAMEWORK.get("NUM_PROXIES") is not None
    assert settings.REST_FRAMEWORK["NUM_PROXIES"] == settings.TRUSTED_PROXY_COUNT


def test_throttle_ident_bypass_reproduced_without_num_proxies(settings):
    """Воспроизведение уязвимости: без NUM_PROXIES каждый новый заголовок —
    новый счётчик. Тест держит описание атаки рядом с исправлением."""
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": None}
    try:
        assert api_settings.NUM_PROXIES is None
        first = _ident("6.6.6.1, 198.51.100.20")
        second = _ident("6.6.6.2, 198.51.100.20")
        assert first != second  # обход предела
    finally:
        api_settings.reload()


def test_throttle_ident_with_one_proxy_ignores_spoofed_prefix(settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    try:
        first = _ident("6.6.6.1, 198.51.100.20")
        second = _ident("6.6.6.2, 198.51.100.20")
        assert first == second == "198.51.100.20"
    finally:
        api_settings.reload()


def test_throttle_ident_with_zero_proxies_uses_connection(settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 0}
    try:
        assert _ident("6.6.6.1", remote="198.51.100.30") == "198.51.100.30"
    finally:
        api_settings.reload()


# --- настройки в отдельном процессе -------------------------------------------
#
# Модули настроек читаются один раз при импорте, поэтому проверяются в
# дочернем процессе с контролируемым окружением. Пустое значение
# переменной намеренно: `_load_dotenv` не перетирает уже заданные ключи,
# и .env разработчика (если он смонтирован) не подменит проверку.

FAKE_PROD_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.production",
    "DJANGO_SECRET_KEY": "fake-" + "x9" * 30,
    "DJANGO_ALLOWED_HOSTS": "api.example.test,crm.example.test",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://crm.example.test",
    "DJANGO_DATABASE_URL": "postgresql://fake:fake@db.invalid:5432/fake",
    "TRUSTED_PROXY_COUNT": "1",
    "SQL_ECHO": "true",
    "TELEGRAM_BOT_API_SECRET": "fake-bot-secret-" + "z" * 32,
}


def _run(code: str, **overrides: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "TRUSTED_PROXY"))}
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_production_requires_database_url():
    result = _run(
        "import django; django.setup()",
        **{**FAKE_PROD_ENV, "DJANGO_DATABASE_URL": ""},
    )
    assert result.returncode != 0
    assert "DJANGO_DATABASE_URL" in result.stderr


def test_development_fallback_has_no_password():
    result = _run(
        "import django; django.setup(); from django.conf import settings; "
        "d = settings.DATABASES['default']; print(repr(d['PASSWORD']), d['HOST'], d['PORT'])",
        DJANGO_SETTINGS_MODULE="config.settings.development",
        DJANGO_DATABASE_URL="",
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split()[0] == "''"


def test_base_settings_contain_no_dsn_with_password():
    text = (BACKEND_ROOT / "config" / "settings" / "base.py").read_text(encoding="utf-8")
    # «схема://пользователь:пароль@» — строка подключения с паролем внутри.
    assert re.search(r"postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+:[^@\s]+@", text) is None


def test_production_settings_hardening():
    result = _run(
        "import django; django.setup(); from django.conf import settings as s; "
        "print(s.REST_FRAMEWORK['NUM_PROXIES'], s.TRUSTED_PROXY_COUNT, "
        "s.LOGGING['loggers']['django.db.backends']['level'], s.DEBUG, "
        "s.SECURE_PROXY_SSL_HEADER[0], len(s.SECRET_KEY_FALLBACKS))",
        **{**FAKE_PROD_ENV, "DJANGO_SECRET_KEY_FALLBACKS": "old-fake-1, old-fake-2 ,"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split() == ["1", "1", "INFO", "False", "HTTP_X_FORWARDED_PROTO", "2"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DJANGO_ALLOWED_HOSTS", "*"),
        ("DJANGO_ALLOWED_HOSTS", " , "),
        ("DJANGO_CSRF_TRUSTED_ORIGINS", "http://crm.example.test"),
    ],
)
def test_production_rejects_unsafe_hosts_and_origins(name, value):
    result = _run("import django; django.setup()", **{**FAKE_PROD_ENV, name: value})
    assert result.returncode != 0
    assert name in result.stderr


@pytest.mark.parametrize("secret", ["", "short-fake-secret", "x" * 31])
def test_production_requires_long_bot_api_secret(secret):
    result = _run(
        "import django; django.setup()",
        **{**FAKE_PROD_ENV, "TELEGRAM_BOT_API_SECRET": secret},
    )
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "TELEGRAM_BOT_API_SECRET" in result.stderr
    # Значение секрета в сообщении об ошибке не печатается.
    if secret:
        assert secret not in result.stderr


def test_production_accepts_bot_api_secret_of_32_chars():
    result = _run(
        "import django; django.setup(); print('ok')",
        **{**FAKE_PROD_ENV, "TELEGRAM_BOT_API_SECRET": "y" * 32},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "ok"


def test_production_check_deploy_is_clean():
    result = _run(
        "import sys; from django.core.management import execute_from_command_line; "
        "execute_from_command_line(['manage.py', 'check', '--deploy', '--fail-level', 'WARNING'])",
        **FAKE_PROD_ENV,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
