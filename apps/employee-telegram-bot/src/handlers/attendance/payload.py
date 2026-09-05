"""Разбор попытки отметки, присланной Mini App через `sendData`.

Всё, что приходит в `web_app_data`, — недоверенные данные. Изменённый
клиент Telegram может отправить туда что угодно: это обычное сообщение
от пользователя, просто служебного вида. Поэтому здесь не «почистить и
пропустить», а **строгий разрешённый список**: лишнее поле отвергает
сообщение целиком.

Отдельно про личность. Единственный Telegram ID, которому можно верить, —
`message.from_user.id`: его проставил сам Telegram, доставляя сообщение.
Всё, что назвалось идентификатором внутри JSON, — попытка представиться
кем-то другим, и она обязана заканчиваться отказом, а не тихим
игнорированием: молча выбросить поле значит не заметить нападение.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

#: Формат payload. Меняется только вместе с Mini App.
VERSION = 1
ACTION = "attendance_scan"

#: Потолок из Bot API. Больше Telegram и сам не доставит.
MAX_BYTES = 4096

#: Ровно эти ключи и ни одного больше.
TOP_LEVEL = {"version", "action", "qr", "client_event_id", "location"}
LOCATION = {"latitude", "longitude", "accuracy"}

MAX_QR_LENGTH = 512
MAX_EVENT_ID_LENGTH = 100
#: Погрешность больше этой не бывает полезной; окончательный потолок —
#: на сервере, здесь только отсев заведомого мусора.
MAX_ACCURACY_M = 100_000


class Rejected(Exception):
    """Payload не прошёл разбор. Причина — для журнала, не для человека."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ScanAttempt:
    """Одна попытка отметки. Ни сотрудника, ни офиса здесь нет."""

    qr: str
    client_event_id: str
    latitude: float
    longitude: float
    accuracy_m: float


def parse(raw: str | None) -> ScanAttempt:
    """Строка из `web_app_data.data` -> проверенная попытка.

    Бросает `Rejected` на любом отклонении. Ничего не «исправляет»:
    payload собирает наш же экран, и расхождение с ним означает либо
    другую версию приложения, либо чужие руки.
    """
    if not raw:
        raise Rejected("empty")
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise Rejected("too_large")

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise Rejected("not_json") from None
    if not isinstance(data, dict):
        raise Rejected("not_an_object")

    # Строгое равенство, а не «содержит нужные». Лишний ключ — повод
    # отказать: он либо от версии, которой мы не знаем, либо подложен.
    if set(data) != TOP_LEVEL:
        raise Rejected("unexpected_fields")
    if data["version"] != VERSION:
        raise Rejected("unknown_version")
    if data["action"] != ACTION:
        raise Rejected("unknown_action")

    qr = _text(data["qr"], MAX_QR_LENGTH, "qr")
    event_id = _text(data["client_event_id"], MAX_EVENT_ID_LENGTH, "client_event_id")

    location = data["location"]
    if not isinstance(location, dict) or set(location) != LOCATION:
        raise Rejected("bad_location")

    latitude = _coordinate(location["latitude"], 90, "latitude")
    longitude = _coordinate(location["longitude"], 180, "longitude")
    accuracy = _accuracy(location["accuracy"])

    return ScanAttempt(
        qr=qr,
        client_event_id=event_id,
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy,
    )


def _text(value, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise Rejected(f"bad_{field}")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        raise Rejected(f"bad_{field}")
    return cleaned


def _coordinate(value, limit: float, field: str) -> float:
    # `bool` — подкласс `int`, и без этой проверки `True` стал бы широтой 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Rejected(f"bad_{field}")
    number = float(value)
    if not math.isfinite(number) or abs(number) > limit:
        raise Rejected(f"bad_{field}")
    return number


def _accuracy(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Rejected("bad_accuracy")
    number = float(value)
    # Ноль и отрицательные значения физически невозможны: это подделка
    # или сбой, и в обоих случаях верить такому измерению нельзя.
    if not math.isfinite(number) or number <= 0 or number > MAX_ACCURACY_M:
        raise Rejected("bad_accuracy")
    return number


__all__ = [
    "ACTION",
    "MAX_BYTES",
    "Rejected",
    "ScanAttempt",
    "VERSION",
    "parse",
]
