"""Проверка подписи Telegram Mini App.

База здесь не нужна: подпись — чистая функция от строки и токена бота.
Именно поэтому проверка и вынесена отдельным модулем — её можно прогнать
по всем краям, не заводя ни организации, ни сотрудника.

Строка `initData` в тестах подписывается тем же алгоритмом, каким её
подписывает Telegram (см. `build_init_data` в conftest). Ни одного обращения
в Telegram при этом не происходит: токен бота заведомо ненастоящий, а сама
проверка — локальный HMAC.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import parse_qsl, urlencode

import pytest

from django_tests.conftest import TEST_BOT_TOKEN, build_init_data
from humotech.telegram.initdata import InitDataError, verify_init_data


def _verify(init_data: str, **kwargs):
    params = {
        "bot_token": TEST_BOT_TOKEN,
        "max_age_seconds": 300,
    }
    params.update(kwargs)
    return verify_init_data(init_data, **params)


def _reason(init_data: str, **kwargs) -> str:
    with pytest.raises(InitDataError) as exc:
        _verify(init_data, **kwargs)
    return exc.value.reason


# --- то, что должно проходить ---

def test_valid_signature_is_accepted():
    verified = _verify(build_init_data(telegram_user_id=555))
    assert verified.user.id == 555
    assert verified.user.username == "ivan"


def test_unicode_in_user_survives_the_check():
    """Кириллица в имени не должна ломать сверку.

    Строка подписывается в UTF-8, а разбирается через `parse_qsl` с
    раскодированием процентов. Ошибка в кодировке на любом из шагов даёт
    несовпадение подписи у совершенно честного пользователя.
    """
    verified = _verify(build_init_data())
    assert verified.user.first_name == "Иван"


# --- подпись ---

def test_wrong_signature_is_rejected():
    """Строка, подписанная чужим токеном бота, не принимается."""
    foreign = build_init_data(bot_token="999999:SOMEONE-ELSES-BOT-TOKEN")
    assert _reason(foreign) == "bad_signature"


def test_hash_missing_is_rejected():
    fields = dict(parse_qsl(build_init_data()))
    fields.pop("hash")
    assert _reason(urlencode(fields)) == "hash_missing"


@pytest.mark.parametrize(
    "field, value",
    [
        ("user", json.dumps({"id": 42, "first_name": "Чужой"})),
        ("auth_date", "1893456000"),
        ("query_id", "AAEsomethingelse"),
    ],
)
def test_any_field_changed_after_signing_is_detected(field, value):
    """Подмена ЛЮБОГО поля после подписи обнаруживается.

    Это главное свойство всей схемы: подпись покрывает все поля, а не
    только те, которые мы читаем. Иначе достаточно взять чужую честную
    строку и заменить в ней `user` на своего.
    """
    tampered = build_init_data(tamper={field: value})
    assert _reason(tampered) == "bad_signature"


def test_added_field_after_signing_is_detected():
    """Дописанное поле тоже ломает подпись: она покрывает весь состав."""
    tampered = build_init_data(tamper={"is_admin": "true"})
    assert _reason(tampered) == "bad_signature"


def test_signature_check_does_not_leak_the_bot_token():
    """Ни токен бота, ни сама строка не попадают в текст исключения.

    Исключения уходят в журналы и в отчёты об ошибках; строка `initData`
    содержит имя и фамилию человека, а токен бота — ключ ко всем подписям.
    """
    init_data = build_init_data(bot_token="999999:SOMEONE-ELSES-BOT-TOKEN")
    with pytest.raises(InitDataError) as exc:
        _verify(init_data)
    text = str(exc.value) + repr(exc.value)
    assert TEST_BOT_TOKEN not in text
    assert "999999" not in text
    assert init_data not in text


# --- срок ---

def test_expired_auth_date_is_rejected():
    old = datetime.now(tz=dt_timezone.utc) - timedelta(seconds=301)
    assert _reason(build_init_data(auth_date=old)) == "expired"


def test_auth_date_just_inside_the_window_is_accepted():
    """Граница проверяется отдельно: ошибка на единицу здесь означала бы,
    что честные запуски иногда отклоняются без всякой причины."""
    recent = datetime.now(tz=dt_timezone.utc) - timedelta(seconds=290)
    assert _verify(build_init_data(auth_date=recent)).user.id == 777_000_111


def test_auth_date_far_in_the_future_is_rejected():
    """Без верхней границы строка из будущего жила бы вечно."""
    ahead = datetime.now(tz=dt_timezone.utc) + timedelta(hours=2)
    assert _reason(build_init_data(auth_date=ahead)) == "auth_date_in_future"


def test_small_clock_skew_is_tolerated():
    """Полминуты расхождения часов — не атака, а обычная жизнь серверов."""
    ahead = datetime.now(tz=dt_timezone.utc) + timedelta(seconds=30)
    assert _verify(build_init_data(auth_date=ahead)).user.id == 777_000_111


def test_auth_date_missing_is_rejected():
    """Строка без `auth_date` подписана честно, но живёт вечно."""
    fields = dict(parse_qsl(build_init_data()))
    del fields["auth_date"]
    # переподписываем без auth_date, иначе отказ придёт по подписи
    import hashlib
    import hmac

    body = {k: v for k, v in fields.items() if k != "hash"}
    check = "\n".join(f"{k}={body[k]}" for k in sorted(body))
    secret = hmac.new(
        b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    body["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    assert _reason(urlencode(body)) == "auth_date_missing"


# --- состав ---

def test_user_missing_is_rejected():
    """Mini App, открытое из инлайн-режима, приходит без `user`.

    Подпись у такой строки верная, но опознать по ней некого — значит,
    и пускать некого.
    """
    import hashlib
    import hmac

    moment = int(datetime.now(tz=dt_timezone.utc).timestamp())
    body = {"auth_date": str(moment), "query_id": "AAEtest"}
    check = "\n".join(f"{k}={body[k]}" for k in sorted(body))
    secret = hmac.new(
        b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    body["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    assert _reason(urlencode(body)) == "user_missing"


def test_non_ascii_hash_is_refused_not_crashed():
    """Подпись приходит от клиента, значит в ней может быть что угодно.

    `hmac.compare_digest` не принимает строки вне ASCII и бросает TypeError:
    прямой вызов превратил бы такой запрос в ошибку 500 вместо честного
    отказа — и записал бы чужое содержимое в журнал.
    """
    assert _reason(build_init_data(tamper={"hash": "подпись-кириллицей"})) == (
        "bad_signature"
    )


def test_empty_string_is_rejected():
    assert _reason("") == "empty"


def test_missing_bot_token_fails_closed():
    """Не настроен токен — отказ, а не «пропустим на этот раз».

    Молчаливый пропуск при отсутствии секрета открыл бы вход кому угодно,
    и заметить это можно было бы только по чужим данным на экране.
    """
    assert _reason(build_init_data(), bot_token="") == "bot_token_not_configured"
