"""Проверка `initData` Telegram Mini App.

Отдельный модуль без базы и без Django-моделей: подпись — чистая функция от
строки и токена бота, и проверять её нужно уметь саму по себе, не поднимая
ни организации, ни сотрудника.

Алгоритм Telegram (core.telegram.org/bots/webapps):

    secret_key = HMAC_SHA256(key="WebAppData", msg=<токен бота>)
    hash       = HMAC_SHA256(key=secret_key, msg=data_check_string)

где `data_check_string` — все пришедшие поля, КРОМЕ `hash`, отсортированные
по имени и склеенные через перевод строки в виде `ключ=значение`. Значения
берутся уже раскодированными из query-строки.

Поле `signature` (подпись Ed25519 для сторонней проверки) из строки НЕ
исключается: в алгоритме с токеном бота Telegram убирает только `hash`.
Так же поступает эталонная реализация в aiogram.

Три вещи, которые здесь важнее самой подписи:

  * подпись сверяется `hmac.compare_digest`. Обычное `==` на строках
    выходит из сравнения на первом несовпавшем байте, и по времени ответа
    хеш подбирается побайтово;
  * `auth_date` проверяется в обе стороны. Просроченная строка — это
    перехваченная строка; строка из будущего означает, что часы одной из
    сторон врут, и без верхней границы такая строка жила бы вечно;
  * `initDataUnsafe` здесь не участвует вовсе. Всё, что нужно, берётся
    из проверенной строки — иначе подпись проверялась бы у одних данных,
    а решение принималось по другим.

Наружу отсюда не уходит ни сама строка, ни токен бота: причина отказа —
короткий код, а не фрагмент входных данных.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from humotech.telegram.compare import constant_time_equal

# Небольшой допуск на расхождение часов между Telegram и нашим сервером.
# Без него минутный сдвиг NTP превращается в «войти невозможно».
CLOCK_SKEW_SECONDS = 60

# Пределы разбора. Настоящая строка Telegram — около килобайта и
# десятка полей (user, chat, auth_date, hash, signature, start_param…).
# Всё, что сильно больше, — не запуск Mini App, а попытка заставить
# сервер разбирать и хешировать мегабайты до проверки подписи.
MAX_INIT_DATA_LENGTH = 8192
MAX_FIELDS = 32


class InitDataError(Exception):
    """Строка не прошла проверку.

    `reason` предназначен для журнала и тестов, а не для пользователя:
    наружу все отказы выглядят одинаково, иначе по разнице ответов
    подбирается и подпись, и состав полей.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class TelegramUser:
    """Пользователь Telegram — только то, что реально пришло в подписи."""

    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None


@dataclass(frozen=True)
class VerifiedInitData:
    user: TelegramUser
    auth_date: datetime
    chat_instance: str | None = None


def _data_check_string(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"{key}={value}" for key, value in sorted(pairs, key=lambda item: item[0])
    )


def _expected_hash(bot_token: str, data_check_string: str) -> str:
    secret_key = hmac.new(
        key=b"WebAppData", msg=bot_token.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def verify_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int,
    now: datetime | None = None,
) -> VerifiedInitData:
    """Проверяет подпись и свежесть. Бросает `InitDataError` при любой беде."""
    if not bot_token:
        # Отказ, а не «пропустим на этот раз»: без токена проверить нечем,
        # и молчаливый пропуск открыл бы вход кому угодно.
        raise InitDataError("bot_token_not_configured")
    if not init_data or not isinstance(init_data, str):
        raise InitDataError("empty")
    if len(init_data) > MAX_INIT_DATA_LENGTH:
        raise InitDataError("too_long")
    try:
        # Одиночный суррогат (`\ud800`) проходит JSON и доходит сюда
        # строкой, но в байты для HMAC не превращается: без этой
        # проверки UnicodeEncodeError ронял бы запрос в 500.
        init_data.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InitDataError("malformed") from exc

    try:
        pairs = parse_qsl(
            init_data,
            strict_parsing=True,
            keep_blank_values=True,
            max_num_fields=MAX_FIELDS,
        )
    except ValueError as exc:
        raise InitDataError("malformed") from exc

    received_hash = None
    rest: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, value in pairs:
        if key == "hash":
            # Двух `hash` быть не может: иначе непонятно, какой проверяем.
            if received_hash is not None:
                raise InitDataError("duplicate_hash")
            received_hash = value
        else:
            # Как и с `hash`: два `user` в одной строке — это вопрос
            # «какой из них настоящий», а решение по подписанным данным
            # не должно зависеть от того, какой возьмёт `dict()`.
            if key in seen:
                raise InitDataError("duplicate_field")
            seen.add(key)
            rest.append((key, value))

    if not received_hash:
        raise InitDataError("hash_missing")

    try:
        expected = _expected_hash(bot_token, _data_check_string(rest))
        signature_ok = constant_time_equal(expected, received_hash)
    except UnicodeEncodeError as exc:
        # Процентная кодировка раскодирована в суррогаты — те же байты
        # не собрать, значит, и подпись не наша.
        raise InitDataError("malformed") from exc
    if not signature_ok:
        raise InitDataError("bad_signature")

    fields = dict(rest)

    raw_auth_date = fields.get("auth_date")
    if not raw_auth_date:
        raise InitDataError("auth_date_missing")
    # Только ASCII-цифры: `int()` принимает и «٣٤٥», и « 12 », и «1_000».
    # Длина ограничена: 12 цифр — это далеко за пределами разумного срока,
    # а больше `datetime` не переварит и бросит OverflowError.
    if not (raw_auth_date.isascii() and raw_auth_date.isdigit()) or len(
        raw_auth_date
    ) > 12:
        raise InitDataError("auth_date_malformed")
    try:
        auth_date = datetime.fromtimestamp(int(raw_auth_date), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise InitDataError("auth_date_malformed") from exc

    moment = now or datetime.now(tz=timezone.utc)
    age = (moment - auth_date).total_seconds()
    if age > max_age_seconds:
        raise InitDataError("expired")
    if age < -CLOCK_SKEW_SECONDS:
        raise InitDataError("auth_date_in_future")

    raw_user = fields.get("user")
    if not raw_user:
        # Mini App, открытое из инлайн-режима, приходит без `user`.
        # Опознать сотрудника по такой строке нельзя, значит и пускать некого.
        raise InitDataError("user_missing")
    try:
        user_data = json.loads(raw_user)
    except (ValueError, RecursionError) as exc:
        # ValueError шире JSONDecodeError: сюда же попадает число длиннее
        # 4300 цифр, которое Python отказывается переводить в int.
        raise InitDataError("user_malformed") from exc
    if not isinstance(user_data, dict):
        raise InitDataError("user_malformed")
    user_id = user_data.get("id")
    # Идентификатор Telegram — положительное целое. `True`, `1.5`, `"12"`
    # и `-5` Telegram не присылает; принимать их значило бы искать
    # в базе сотрудника по тому, что мы сами дорисовали.
    if type(user_id) is not int or user_id <= 0:
        raise InitDataError("user_malformed")

    return VerifiedInitData(
        user=TelegramUser(
            id=user_id,
            username=_text(user_data.get("username")),
            first_name=_text(user_data.get("first_name")),
            last_name=_text(user_data.get("last_name")),
            language_code=_text(user_data.get("language_code")),
        ),
        auth_date=auth_date,
        chat_instance=_text(fields.get("chat_instance")),
    )


def _text(value) -> str | None:
    """Строка или ничего: число или объект на месте имени не пропускаем."""
    return value if isinstance(value, str) else None
