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
from django.core.exceptions import ValidationError as DjangoValidationError

# 32 байта случайности -> 43 символа в безопасном для URL алфавите.
# Telegram кладёт полезную нагрузку `?start=` в 64 символа и принимает
# только [A-Za-z0-9_-], поэтому «link_» + 43 = 48 помещается с запасом.
TOKEN_BYTES = 32
TOKEN_PREFIX = "link_"

# Второй префикс — для ссылки на первичное ознакомление. Токен и таблица
# те же самые: приглашение одно, разное у них только то, что человека
# ждёт после привязки. Заводить вторую таблицу ссылок значило бы сделать
# две двери в один дом и закрывать за собой обе.
#
# «onboarding_» + 43 символа = 54: в шестидесяти четырёх, которые Telegram
# отдаёт под полезную нагрузку `/start`, помещается.
ONBOARDING_PREFIX = "onboarding_"

#: Все префиксы, по которым бот узнаёт ссылку привязки.
LINK_PREFIXES = (TOKEN_PREFIX, ONBOARDING_PREFIX)

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

    Оба префикса ведут к одному и тому же токену: ссылка на ознакомление
    отличается не содержимым, а тем, что бот покажет после привязки.
    """
    if not payload:
        return None
    payload = payload.strip()
    for prefix in LINK_PREFIXES:
        if payload.startswith(prefix):
            return payload[len(prefix):] or None
    return None


def build_invitation_link(
    bot_username: str, token: str, *, prefix: str = TOKEN_PREFIX
) -> str:
    """Ссылка, которую HR передаёт сотруднику."""
    return f"https://t.me/{bot_username}?start={prefix}{token}"


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


#: Длиннее честный токен не бывает: четыре коротких поля в base64 плюс
#: подпись — около трёхсот символов. Разбирать мегабайт из заголовка
#: незачем.
MAX_TOKEN_LENGTH = 2048


def _issued_at(token: str) -> int | None:
    """Момент выпуска из УЖЕ проверенного токена.

    Формат `django.core.signing`: `<данные>:<время base62>:<подпись>`.
    Время подписано вместе с данными, поэтому после успешного
    `signing.loads` ему можно верить.
    """
    try:
        return signing.b62_decode(token.rsplit(":", 2)[1])
    except (IndexError, ValueError):
        return None


def _predates_link(telegram_account_id: str, issued_at: int) -> bool:
    """Выпущен ли токен раньше текущей привязки.

    Без этой проверки отзыв не был окончательным. Строка привязки
    у сотрудника одна: после отключения и повторной привязки ТОГО ЖЕ
    Telegram совпадают и `telegram_account_id`, и `telegram_user_id`,
    и токен, снятый с украденного телефона до отзыва, снова открывал
    кабинет до конца своих двенадцати часов.

    Каждая активация (подтверждение HR, согласие в боте) переписывает
    `connected_at`, поэтому всё, что выпущено раньше, отвергается.
    Сравнение по целым секундам: время в токене без долей, и токен,
    выданный в ту же секунду, что и привязка, должен остаться рабочим.
    """
    from humotech.telegram.models import TelegramAccount

    connected_at = (
        TelegramAccount.objects.filter(id=telegram_account_id)
        .values_list("connected_at", flat=True)
        .first()
    )
    if connected_at is None:
        # Привязки нет — решать будет `resolve`, он её и не найдёт.
        return False
    return issued_at < int(connected_at.timestamp())


def read_mini_app_token(token: str, *, max_age_seconds: int) -> MiniAppClaims | None:
    """Разбирает токен. None — подпись не сошлась, срок истёк или токен
    выпущен до текущей привязки.

    Причина отказа наружу не выносится намеренно: клиенту во всех случаях
    делать одно и то же — открыть Mini App заново.
    """
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    try:
        data = signing.loads(token, salt=MINI_APP_SALT, max_age=max_age_seconds)
    except (signing.BadSignature, ValueError, TypeError):
        # ValueError/TypeError — на случай мусора, который пройдёт проверку
        # формата, но не декодирование; подпись к этому моменту уже
        # сверена, так что сюда попадает только испорченная своя строка.
        return None
    if not isinstance(data, dict):
        return None
    try:
        claims = MiniAppClaims(
            telegram_account_id=str(data["telegram_account_id"]),
            telegram_user_id=int(data["telegram_user_id"]),
            employee_id=str(data["employee_id"]),
            organization_id=str(data["organization_id"]),
        )
    except (KeyError, TypeError, ValueError):
        # Подпись своя, но состав не тот: старый формат или чужая цель.
        return None

    issued_at = _issued_at(token)
    if issued_at is None:
        return None
    try:
        if _predates_link(claims.telegram_account_id, issued_at):
            return None
    except (ValueError, DjangoValidationError):
        # Идентификатор привязки не UUID. Подписать такое можно только
        # нашим ключом, но 500 из-за этого всё равно не нужен.
        return None
    return claims
