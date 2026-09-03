"""Должность. Общая для всей организации, не привязана к офису."""

from __future__ import annotations

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import (
    ArchivableMixin,
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import POSITION_STATUSES, in_check


class Position(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, ArchivableMixin, Base
):
    __tablename__ = "positions"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_positions_org_code"),
        in_check("status", POSITION_STATUSES, "status"),
    )
