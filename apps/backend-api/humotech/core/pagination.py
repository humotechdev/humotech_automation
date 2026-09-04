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

from django.db.models import Q, QuerySet

from humotech.core.errors import ValidationFailed

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

T = TypeVar("T")


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
            payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
            return cls(
                created_at=datetime.fromisoformat(payload["created_at"]),
                id=uuid.UUID(payload["id"]),
            )
        except (
            binascii.Error, ValueError, KeyError, TypeError, json.JSONDecodeError
        ) as exc:
            # курсор приходит от клиента: испорченный не должен ронять запрос
            raise ValidationFailed(
                "Некорректный курсор постраничного вывода", details={"cursor": raw}
            ) from exc


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
            payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
            return cls(value=str(payload["v"]), id=uuid.UUID(payload["id"]))
        except (
            binascii.Error, ValueError, KeyError, TypeError, json.JSONDecodeError
        ) as exc:
            raise ValidationFailed(
                "Некорректный курсор постраничного вывода", details={"cursor": raw}
            ) from exc


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
        operator = "lt" if descending else "gt"
        queryset = queryset.filter(
            Q(**{f"{field}__{operator}": position.value})
            | Q(**{field: position.value, f"id__{operator}": position.id})
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
