"""Одноразовый токен привязки и внутренний токен доступа Mini App.

Два разных секрета, и путать их нельзя:

  * **токен приглашения** — случайные 256 бит. Сервер его не помнит: в базе
    лежит SHA-256, и проверка идёт сравнением хешей. Утечка дампа не даёт
    ни одной рабочей ссылки;
  * **токен Mini App** — подписанная сервером строка (`django.core.signing`).
    Здесь наоборот: сервер обязан уметь её прочитать, потому что внутри
    лежит, кому она выдана. Секрет — `SECRET_KEY`, подпись проверяется
    на каждом запросе.

Почему не argon2 для первого. Медленный хеш нужен там, где секрет выбирает
человек и его можно перебрать. Здесь секрет выбирает `secrets.token_urlsafe`,
перебирать нечего, а проверка выполняется на каждом переходе по ссылке —
медленный хеш стал бы способом положить сервер, ничего не защитив.

Почему не таблица токенов для второго. Хранить нечего: подпись сама
удостоверяет содержимое. Отзыв работает не через список выданных токенов,
а через состояние привязки — оно перечитывается на каждом запросе.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from django.core import signing

# 32 байта случайности -> 43 символа в безопасном для URL алфавите.
# Telegram кладёт полезную нагрузку `?start=` в 64 символа и принимает
# только [A-Za-z0-9_-], поэтому «link_» + 43 = 48 помещается с запасом.
TOKEN_BYTES = 32
TOKEN_PREFIX = "link_"

# Подпись Mini App живёт в своём пространстве имён: строка, подписанная
# для одной цели, не должна приниматься в другой.
MINI_APP_SALT = "humotech.telegram.mini-app"


def generate_invitation_token() -> str:
    """Открытый токен. Возвращается ОДИН раз и нигде не сохраняется."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_invitation_token(token: str) -> str:
    """SHA-256 в шестнадцатеричном виде — то, что попадает в базу."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_start_payload(payload: str | None) -> str | None:
    """Токен из полезной нагрузки `/start`, или None если это не ссылка привязки.

    Разбор нарочно строгий: посторонняя нагрузка не должна случайно
    оказаться «почти токеном» и уйти в базу на поиск.
    """
    if not payload:
        return None
    payload = payload.strip()
    if not payload.startswith(TOKEN_PREFIX):
        return None
    token = payload[len(TOKEN_PREFIX):]
    return token or None


def build_invitation_link(bot_username: str, token: str) -> str:
    """Ссылка, которую HR передаёт сотруднику."""
    return f"https://t.me/{bot_username}?start={TOKEN_PREFIX}{token}"


@dataclass(frozen=True)
class MiniAppClaims:
    """Что подписано в токене Mini App.

    `telegram_user_id` здесь не для удобства: строка привязки у сотрудника
    одна и переиспользуется при перепривязке, поэтому её идентификатор
    НЕ является устойчивым признаком «того самого» Telegram. Без сверки
    с текущим значением старый токен продолжал бы работать после того,
    как HR отключил привязку и выдал её другому аккаунту.
    """

    telegram_account_id: str
    telegram_user_id: int
    employee_id: str
    organization_id: str


def issue_mini_app_token(claims: MiniAppClaims) -> str:
    return signing.dumps(
        {
            "telegram_account_id": claims.telegram_account_id,
            "telegram_user_id": claims.telegram_user_id,
            "employee_id": claims.employee_id,
            "organization_id": claims.organization_id,
        },
        salt=MINI_APP_SALT,
    )


def read_mini_app_token(token: str, *, max_age_seconds: int) -> MiniAppClaims | None:
    """Разбирает токен. None — подпись не сошлась либо срок истёк.

    Причина отказа наружу не выносится намеренно: клиенту в обоих случаях
    делать одно и то же — открыть Mini App заново.
    """
    try:
        data = signing.loads(token, salt=MINI_APP_SALT, max_age=max_age_seconds)
    except signing.BadSignature:
        return None
    try:
        return MiniAppClaims(
            telegram_account_id=str(data["telegram_account_id"]),
            telegram_user_id=int(data["telegram_user_id"]),
            employee_id=str(data["employee_id"]),
            organization_id=str(data["organization_id"]),
        )
    except (KeyError, TypeError, ValueError):
        # Подпись своя, но состав не тот: старый формат или чужая цель.
        return None
