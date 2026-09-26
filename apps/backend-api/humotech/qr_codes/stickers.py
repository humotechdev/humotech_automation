"""Печатный QR-код точки: что в нём лежит и как его узнать при скане.

Меняющийся код на экране (`tokens.py`) подписан и живёт полминуты.
Печатный — наоборот: наклейка висит на стене месяцами, и подписывать
его сроком бессмысленно. Поэтому в нём лежит только случайный секрет
точки, а всё остальное — офис, направление, включена ли точка — сервер
берёт из базы по хешу этого секрета.

**Что в коде.** Ссылка на бота: `https://t.me/<бот>?start=qr_<секрет>`.
Ссылка, а не голая строка, — чтобы код работал и из обычной камеры
телефона: она откроет Telegram, бот попросит геопозицию и отметит. Из
«📷 Отметиться» в самом боте тот же код тоже читается — ссылку разбирает
сервер.

**Чего в коде нет.** Ни идентификатора офиса, ни названия точки, ни
сотрудника. Наклейку фотографируют; всё, что на ней, считается
опубликованным. Сам секрет в базе не хранится — только SHA-256, и
перевыпуск меняет хеш: прежняя наклейка перестаёт действовать сразу.

**Чем он защищён, раз срока нет.** Геозоной. Отметка по печатному коду
принимается только с координатами и только внутри радиуса офиса; у
офиса без точки на карте печатный код не работает вовсе. Фотография
наклейки, отправленная домой, без координат офиса ничего не даёт.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from django.conf import settings

#: Префикс полезной нагрузки `/start`. Telegram пропускает в ней до 64
#: символов из [A-Za-z0-9_-]: «qr_» и секрет в 43 символа укладываются.
PREFIX = "qr_"

#: Секрет — `secrets.token_urlsafe(32)`: 43 символа base64url.
_SECRET = re.compile(r"^[A-Za-z0-9_-]{32,61}$")

#: Откуда может прийти ссылка. Посторонний адрес с похожим параметром —
#: не наш код, и разбирать его как наш незачем.
_HOSTS = {"t.me", "telegram.me", "www.t.me"}


def token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def payload(secret: str) -> str:
    """Полезная нагрузка `/start` для секрета."""
    return f"{PREFIX}{secret}"


def link(secret: str) -> str | None:
    """Ссылка для наклейки. `None`, если имя бота не настроено.

    Без имени бота ссылку не собрать, и выдумывать его нельзя: код с
    чужим ботом на стене хуже, чем никакого.
    """
    username = (settings.TELEGRAM.get("BOT_USERNAME") or "").lstrip("@")
    if not username:
        return None
    return f"https://t.me/{username}?start={payload(secret)}"


def secret_of(raw: str | None) -> str | None:
    """Секрет печатного кода из отсканированной строки, либо `None`.

    Понимает три вида: голую нагрузку `qr_…`, ссылку `https://t.me/…?
    start=qr_…` и `tg://resolve?…&start=qr_…`. Всё остальное — не
    печатный код: меняющийся код экрана или посторонний QR.
    """
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    lowered = value.lower()
    if lowered.startswith(("https://", "http://")):
        parts = urlsplit(value)
        if (parts.hostname or "").lower() not in _HOSTS:
            return None
        value = (parse_qs(parts.query).get("start") or [""])[0]
    elif lowered.startswith("tg://"):
        value = (parse_qs(urlsplit(value).query).get("start") or [""])[0]

    if not value.startswith(PREFIX):
        return None
    secret = value[len(PREFIX):]
    return secret if _SECRET.match(secret) else None


__all__ = ["PREFIX", "link", "payload", "secret_of", "token_hash"]
