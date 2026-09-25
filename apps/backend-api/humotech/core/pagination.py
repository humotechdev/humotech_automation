"""Постраничный вывод списков — keyset, а не OFFSET.

OFFSET здесь не годится по двум причинам. Во-первых, он линейно замедляется:
`OFFSET 10000` заставляет PostgreSQL прочитать и выбросить десять тысяч строк.
Во-вторых, между запросом первой и второй страницы кто-то создаёт сотрудника —
все последующие строки сдвигаются, и одна запись показывается дважды, а другая
не показывается вовсе.

Keyset берёт строки строго «после» последней показанной. Ключ обязан быть
уникальным, поэтому сортируем по паре `(created_at, id)`: у записей, созданных
одной транзакцией, `created_at` совпадает до микросекунды (в схеме `now()`,
а не `statement_timestamp()`) — по одному только времени часть строк
потерялась бы.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q, QuerySet

from humotech.core.errors import ValidationFailed

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

T = TypeVar("T")

# Наш курсор — это base64 от JSON в сотню байт. Всё, что заметно длиннее,
# заведомо не наше, и разбирать его незачем.
MAX_CURSOR_LENGTH = 512

# Всё, чем может закончиться разбор чужой строки: кривой base64, не JSON,
# JSON не той формы (список, число, строка), JSON глубиной в тысячу
# уровней, дата за краем календаря, `id` не строкой.
_CURSOR_ERRORS = (
    binascii.Error, ValueError, KeyError, TypeError, AttributeError,
    json.JSONDecodeError, RecursionError, OverflowError,
)


def _bad_cursor(raw) -> ValidationFailed:
    # Ввод клиента в ответе режется: эхо мегабайтной строки ни к чему.
    return ValidationFailed(
        "Некорректный курсор постраничного вывода",
        details={"cursor": str(raw)[:100]},
    )


def _load_cursor(raw: str) -> dict:
    if len(raw) > MAX_CURSOR_LENGTH:
        raise ValueError("cursor too long")
    payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
    if not isinstance(payload, dict):
        raise TypeError("cursor is not an object")
    return payload


@dataclass(frozen=True)
class Cursor:
    """Позиция последней показанной строки."""

    created_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        payload = json.dumps(
            {"created_at": self.created_at.isoformat(), "id": str(self.id)}
        )
        return base64.urlsafe_b64encode(payload.encode()).decode()

    @classmethod
    def decode(cls, raw: str) -> "Cursor":
        try:
            payload = _load_cursor(raw)
            created_at = datetime.fromisoformat(payload["created_at"])
            if created_at.tzinfo is None:
                # Наивное время сравнилось бы с timestamptz по поясу сервера:
                # курсор, выданный нами, всегда с поясом — значит, чужой.
                raise ValueError("cursor without timezone")
            return cls(created_at=created_at, id=uuid.UUID(payload["id"]))
        except _CURSOR_ERRORS as exc:
            # курсор приходит от клиента: испорченный не должен ронять запрос
            raise _bad_cursor(raw) from exc


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    next_cursor: str | None
    has_more: bool

    @property
    def size(self) -> int:
        return len(self.items)


def normalize_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    if limit < 1:
        raise ValidationFailed("Размер страницы должен быть положительным",
                               details={"limit": limit})
    return min(limit, MAX_PAGE_SIZE)


def paginate(
    queryset: QuerySet,
    *,
    limit: int | None = None,
    cursor: str | None = None,
) -> Page:
    """Одна страница по убыванию `(created_at, id)`."""
    size = normalize_limit(limit)
    queryset = queryset.order_by("-created_at", "-id")

    if cursor:
        position = Cursor.decode(cursor)
        # Разложенное сравнение кортежей: строго «раньше по времени» ЛИБО
        # «то же время, но меньший id». Одного условия по времени мало —
        # у записей одной транзакции время совпадает.
        queryset = queryset.filter(
            Q(created_at__lt=position.created_at)
            | Q(created_at=position.created_at, id__lt=position.id)
        )

    # берём на одну строку больше запрошенного: так становится известно,
    # есть ли следующая страница, без отдельного COUNT(*)
    rows = list(queryset[: size + 1])
    has_more = len(rows) > size
    items = rows[:size]

    next_cursor = (
        Cursor(created_at=items[-1].created_at, id=items[-1].id).encode()
        if has_more and items
        else None
    )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


@dataclass(frozen=True)
class FieldCursor:
    """Позиция по произвольному полю плюс `id`.

    `paginate` листает по времени создания, и для большинства списков это
    верно. Но не для всех: календарь читают по датам, а не по тому, когда
    строку завели, и «следующая страница» там обязана означать «следующие
    дни». Ключ по-прежнему пара с `id` — по одной дате строки нескольких
    офисов не различить.
    """

    value: str
    id: uuid.UUID

    def encode(self) -> str:
        payload = json.dumps({"v": self.value, "id": str(self.id)})
        return base64.urlsafe_b64encode(payload.encode()).decode()

    @classmethod
    def decode(cls, raw: str) -> "FieldCursor":
        try:
            payload = _load_cursor(raw)
            value = payload["v"]
            if not isinstance(value, str) or "\x00" in value:
                raise TypeError("cursor value is not a plain string")
            return cls(value=value, id=uuid.UUID(payload["id"]))
        except _CURSOR_ERRORS as exc:
            raise _bad_cursor(raw) from exc


def paginate_on(
    queryset: QuerySet,
    field: str,
    *,
    descending: bool = False,
    limit: int | None = None,
    cursor: str | None = None,
) -> Page:
    """Одна страница по паре `(field, id)`.

    Поле обязано быть сравнимым и стабильным: сортировка по изменяемому
    значению приводит ровно к той беде, от которой keyset и защищает —
    строка переезжает между страницами.
    """
    size = normalize_limit(limit)
    direction = "-" if descending else ""
    queryset = queryset.order_by(f"{direction}{field}", f"{direction}id")

    if cursor:
        position = FieldCursor.decode(cursor)
        # Значение приводится к типу поля заранее: иначе строка «abc» в
        # курсоре календаря дошла бы до ORM и уронила запрос на сравнении
        # с датой. Подпись у курсора нет — его может собрать кто угодно.
        try:
            value = queryset.model._meta.get_field(field).to_python(position.value)
        except (DjangoValidationError, ValueError, TypeError, OverflowError) as exc:
            raise _bad_cursor(cursor) from exc
        operator = "lt" if descending else "gt"
        queryset = queryset.filter(
            Q(**{f"{field}__{operator}": value})
            | Q(**{field: value, f"id__{operator}": position.id})
        )

    rows = list(queryset[: size + 1])
    has_more = len(rows) > size
    items = rows[:size]

    next_cursor = (
        FieldCursor(value=str(getattr(items[-1], field)), id=items[-1].id).encode()
        if has_more and items
        else None
    )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)
