"""Офис — самостоятельный объект: свой адрес, свой часовой пояс, свои QR-точки."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import CIDR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    ArchivableMixin,
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import OFFICE_STATUSES, in_check

if TYPE_CHECKING:
    from src.modules.departments.models import Department
    from src.modules.organizations.models import Organization
    from src.modules.qr_codes.models import OfficeQrPoint
    from src.modules.regions.models import Region


class Office(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, ArchivableMixin, Base
):
    __tablename__ = "offices"

    region_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    # Часовой пояс офиса обязателен: все TIMESTAMPTZ хранятся в UTC,
    # а «опоздал / ушёл раньше» считается в локальном времени офиса.
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    geofence_radius_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    opened_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="offices")
    region: Mapped["Region"] = relationship(back_populates="offices")
    departments: Mapped[list["Department"]] = relationship(back_populates="office")
    networks: Mapped[list["OfficeNetwork"]] = relationship(back_populates="office")
    qr_points: Mapped[list["OfficeQrPoint"]] = relationship(back_populates="office")

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_offices_org_code"),
        in_check("status", OFFICE_STATUSES, "status"),
        CheckConstraint(
            "geofence_radius_m IS NULL OR geofence_radius_m > 0",
            name="geofence_positive",
        ),
        CheckConstraint(
            "closed_at IS NULL OR opened_at IS NULL OR closed_at >= opened_at",
            name="close_after_open",
        ),
    )


class OfficeNetwork(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Разрешённые сети офиса — дополнительная проверка при QR-отметке."""

    __tablename__ = "office_networks"

    office_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    network_cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    office: Mapped["Office"] = relationship(back_populates="networks")

    __table_args__ = (
        UniqueConstraint("office_id", "network_cidr", name="uq_office_networks_cidr"),
    )
