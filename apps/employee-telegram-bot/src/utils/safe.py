"""Как обращаться с тем, что пришло не из кода бота.

Бот работает с `parse_mode=HTML` по умолчанию (`main.py`). Значит, любая
строка, пришедшая с сервера или от человека, — имя, название офиса,
комментарий кадровика, ответ ассистента, — попав в сообщение без
экранирования, становится разметкой:

  * `<` в обычном тексте («стаж < 1 года») ломает отправку целиком —
    Telegram отвечает «can't parse entities», и человек не получает ничего;
  * `<a href="...">Портал HR</a>` в комментарии или в ответе ассистента
    превращается в настоящую ссылку с подменённым адресом;
  * `<tg-spoiler>` и незакрытые теги прячут часть ответа.

Поэтому правило одно: всё чужое проходит через `escape`, а разметку
пишет только код бота.

Второе правило — про идентификаторы, которые бот вставляет в адрес
запроса к backend (`/me/absences/<id>/document`). Они приходят из
`callback_data` и из очереди. Сервер всё равно проверяет владельца, но
строка вида `../../telegram/bot/outbox` не должна даже уходить в запрос:
клиент HTTP нормализует `..`, и запрос с общим секретом бота ушёл бы
на чужой адрес.
"""

from __future__ import annotations

import html
import re
import unicodedata

_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def escape(value) -> str:
    """Текст для HTML-сообщения Telegram. `None` — пустая строка."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def is_uuid(value) -> bool:
    """Строка — ровно UUID и ничего больше."""
    # fullmatch, а не match: `$` в `match` пропускает хвостовой «\n».
    return isinstance(value, str) and bool(_UUID.fullmatch(value))


#: Предел длины имени файла, которое уходит на сервер.
MAX_FILENAME = 120


def safe_filename(name: str | None, fallback: str = "file") -> str:
    """Имя файла без пути, управляющих символов и лишней длины.

    Имя придумал отправитель: в нём бывает `../`, обратные слэши, символы
    смены направления текста (`\\u202e`, «spravka\\u202egpj.exe») и
    переводы строк, которые ломают заголовок multipart.
    """
    raw = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(
        char for char in raw
        if unicodedata.category(char)[0] != "C"  # Cc, Cf, Cs, Co, Cn
    ).strip().lstrip(".")
    if not cleaned:
        return fallback
    if len(cleaned) > MAX_FILENAME:
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and 0 < len(suffix) <= 10:
            cleaned = stem[: MAX_FILENAME - len(suffix) - 1] + "." + suffix
        else:
            cleaned = cleaned[:MAX_FILENAME]
    return cleaned


__all__ = ["MAX_FILENAME", "escape", "is_uuid", "safe_filename"]
