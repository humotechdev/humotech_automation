"""Регион — группа офисов. Основа регионального разграничения прав HR."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    ArchivableMixin,
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import REGION_STATUSES, in_check

if TYPE_CHECKING:
    from src.modules.offices.models import Office
    from src.modules.organizations.models import Organization


class Region(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, ArchivableMixin, Base
):
    __tablename__ = "regions"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # NULL — регион наследует organizations.default_timezone
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="regions")
    offices: Mapped[list["Office"]] = relationship(back_populates="region")

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_regions_org_code"),
        in_check("status", REGION_STATUSES, "status"),
    )
