"""Контракт справочника офисов."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class OfficeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    region_id: uuid.UUID
    code: str
    name: str
    address: str
    timezone: str
    latitude: Decimal | None
    longitude: Decimal | None
    geofence_radius_m: int | None
    status: str
    opened_at: date | None
    closed_at: date | None
    created_at: datetime
    updated_at: datetime


class OfficeListItem(OfficeView):
    """Строка списка вместе с названием региона — чтобы не ходить за ним
    отдельным запросом на каждую строку."""

    region_code: str | None = None
    region_name: str | None = None


class OfficeCreateRequest(BaseModel):
    region_id: uuid.UUID
    code: str
    name: str
    address: str
    timezone: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    geofence_radius_m: int | None = None
    opened_at: date | None = None


class OfficeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_id: uuid.UUID | None = None
    code: str | None = None
    name: str | None = None
    address: str | None = None
    timezone: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    geofence_radius_m: int | None = None
    opened_at: date | None = None
    closed_at: date | None = None
