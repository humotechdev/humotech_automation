"""Контракт справочника регионов.

Наружу отдаётся не ORM-объект, а pydantic-модель: у ORM-объекта есть ленивые
связи, и любое обращение к ним за пределами сессии либо делает лишний запрос,
либо падает. Явный слой представления делает состав ответа видимым.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RegionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    code: str
    name: str
    timezone: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class RegionListItem(RegionView):
    """Строка списка: плюс число офисов, посчитанное одним запросом на страницу."""

    offices_count: int = 0


class RegionCreateRequest(BaseModel):
    code: str
    name: str
    timezone: str | None = None


class RegionUpdateRequest(BaseModel):
    """Не переданное поле не меняется. Отличать «не передано» от «передан null»
    важно: `timezone=None` означает «наследовать пояс организации»."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    name: str | None = None
    timezone: str | None = None
