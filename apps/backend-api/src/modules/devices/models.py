"""Устройства сотрудника — защита от передачи QR другому человеку.

Идентификатор устройства хранится ТОЛЬКО как hash. IMEI, MAC, рекламные
идентификаторы и прочие лишние персональные данные не собираются и не хранятся.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import DEVICE_STATUSES, in_check

if TYPE_CHECKING:
    from src.modules.employees.models import Employee


class EmployeeDevice(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    __tablename__ = "employee_devices"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    device_identifier_hash: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str | None] = mapped_column(String(30), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trusted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    employee: Mapped["Employee"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "device_identifier_hash", name="uq_employee_devices_hash"
        ),
        in_check("status", DEVICE_STATUSES, "status"),
    )
