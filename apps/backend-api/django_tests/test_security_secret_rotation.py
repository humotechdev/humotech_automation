"""Смена DJANGO_SECRET_KEY: поведение боевых настроек и инструкция к нему.

Прежний ключ стенда попал в журнал сессии аудита. Инструкция
(docs/security/rotate-telegram-token.md, раздел 3) требует менять его
«жёстко»: DJANGO_SECRET_KEY_FALLBACKS пустой, хэш утёкшего ключа — в
DJANGO_REVOKED_SECRET_KEY_SHA256. Здесь проверено:

* что делают эти переменные на самом деле (боевые настройки в отдельном
  процессе и подписи в этом);
* что инструкция говорит то же самое. Раньше она утверждала, что
  production.py запасные ключи не читает, хотя он их уже читал; тест ниже
  не даст им разойтись снова.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from django.core import signing

from django_tests.test_security_infra import BACKEND_ROOT, FAKE_PROD_ENV, _run
from humotech.telegram.tokens import MINI_APP_SALT

OLD_KEY = "fake-leaked-" + "a1" * 30
NEW_KEY = "fake-fresh-" + "b2" * 30


def sha(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _prod(**overrides: str):
    return _run(
        "import django; django.setup(); from django.conf import settings as s; "
        "print(len(s.SECRET_KEY_FALLBACKS))",
        **{**FAKE_PROD_ENV, **overrides},
    )


# --- боевые настройки ---------------------------------------------------------


def test_production_reads_fallbacks():
    result = _prod(DJANGO_SECRET_KEY=NEW_KEY, DJANGO_SECRET_KEY_FALLBACKS=f"{OLD_KEY}, ")
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "1"


def test_rotation_after_leak_starts():
    """Шаги инструкции: новый ключ, FALLBACKS пуст, хэш старого — в REVOKED."""
    result = _prod(
        DJANGO_SECRET_KEY=NEW_KEY,
        DJANGO_SECRET_KEY_FALLBACKS="",
        DJANGO_REVOKED_SECRET_KEY_SHA256=sha(OLD_KEY),
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "0"


@pytest.mark.parametrize(
    "overrides",
    [
        # утёкший ключ оставили в запасных — именно то, что запрещает инструкция
        {"DJANGO_SECRET_KEY": NEW_KEY, "DJANGO_SECRET_KEY_FALLBACKS": OLD_KEY},
        # утёкший ключ вернули в основной
        {"DJANGO_SECRET_KEY": OLD_KEY, "DJANGO_SECRET_KEY_FALLBACKS": ""},
    ],
    ids=["leaked-in-fallbacks", "leaked-as-current"],
)
def test_revoked_key_blocks_start(overrides):
    result = _prod(DJANGO_REVOKED_SECRET_KEY_SHA256=f"{sha('fake-other')},{sha(OLD_KEY).upper()}", **overrides)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    # Ни ключ, ни его хэш в сообщение не попадают.
    assert OLD_KEY not in result.stderr and sha(OLD_KEY) not in result.stderr.lower()


def test_current_key_in_fallbacks_is_refused():
    result = _prod(DJANGO_SECRET_KEY=NEW_KEY, DJANGO_SECRET_KEY_FALLBACKS=NEW_KEY)
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY_FALLBACKS" in result.stderr
    assert NEW_KEY not in result.stderr


def test_raw_key_in_revoked_list_is_refused_without_echo():
    """В REVOKED по ошибке вставили сам ключ, а не хэш."""
    result = _prod(DJANGO_SECRET_KEY=NEW_KEY, DJANGO_REVOKED_SECRET_KEY_SHA256=OLD_KEY)
    assert result.returncode != 0
    assert "DJANGO_REVOKED_SECRET_KEY_SHA256" in result.stderr
    assert OLD_KEY not in result.stderr


# --- что означает ключ в FALLBACKS для подписей -------------------------------


def test_fallback_keeps_old_signatures_alive(settings):
    """Почему утёкший ключ нельзя класть в запасные: подпись им принимается.

    Токен Mini App подписан так же, как сессии CRM, — ключом Django.
    """
    settings.SECRET_KEY = OLD_KEY
    settings.SECRET_KEY_FALLBACKS = []
    forged = signing.dumps({"employee_id": "fake"}, salt=MINI_APP_SALT)

    settings.SECRET_KEY = NEW_KEY
    settings.SECRET_KEY_FALLBACKS = [OLD_KEY]
    assert signing.loads(forged, salt=MINI_APP_SALT)["employee_id"] == "fake"

    settings.SECRET_KEY_FALLBACKS = []
    with pytest.raises(signing.BadSignature):
        signing.loads(forged, salt=MINI_APP_SALT)


# --- инструкция не расходится с кодом ----------------------------------------


def _doc() -> str:
    candidates = [
        os.environ.get("HUMOTECH_DOCS_DIR", ""),
        str(BACKEND_ROOT.parent.parent / "docs"),  # запуск из репозитория
        "/docs",  # контейнер: -v <repo>/docs:/docs:ro
    ]
    for base in filter(None, candidates):
        path = Path(base) / "security" / "rotate-telegram-token.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    pytest.skip("docs/ не смонтирован: запустите с -v <repo>/docs:/docs:ro")


def test_settings_read_the_variables_the_doc_names():
    source = (BACKEND_ROOT / "config" / "settings" / "production.py").read_text(encoding="utf-8")
    assert 'env("DJANGO_SECRET_KEY_FALLBACKS"' in source
    assert 'env("DJANGO_REVOKED_SECRET_KEY_SHA256"' in source


def test_doc_matches_settings_behaviour():
    doc = _doc()
    section = doc.split("## 3. `DJANGO_SECRET_KEY`", 1)[1].split("\n## 4.", 1)[0]
    # Старое неверное утверждение не вернулось.
    assert "не читает" not in section
    # Обе переменные описаны, и для утечки FALLBACKS — пустой.
    assert "DJANGO_SECRET_KEY_FALLBACKS" in section
    assert "DJANGO_REVOKED_SECRET_KEY_SHA256" in section
    assert "`DJANGO_SECRET_KEY_FALLBACKS=` — **пусто**" in section
    assert "класть **нельзя**" in section
    # Последствия для людей названы явно.
    assert "войти в CRM заново" in section
    assert "заново открыть Mini App" in section
