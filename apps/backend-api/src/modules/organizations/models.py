"""Организация — корень изоляции данных. Всё остальное живёт под ней."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    ArchivableMixin,
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import ORGANIZATION_STATUSES, in_check

if TYPE_CHECKING:
    from src.modules.employees.models import Employee
    from src.modules.offices.models import Office
    from src.modules.regions.models import Region
    from src.modules.users.models import User


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "organizations"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    # Счётчик ревизии базы знаний. Растёт при каждой публикации или архивации
    # источника и входит в ключ кэша — поэтому после публикации нового правила
    # весь старый кэш ответов автоматически перестаёт использоваться,
    # без обхода и удаления ключей.
    knowledge_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )

    regions: Mapped[list["Region"]] = relationship(back_populates="organization")
    offices: Mapped[list["Office"]] = relationship(back_populates="organization")
    employees: Mapped[list["Employee"]] = relationship(back_populates="organization")
    users: Mapped[list["User"]] = relationship(back_populates="organization")
    settings: Mapped[list["OrganizationSetting"]] = relationship(
        back_populates="organization"
    )

    __table_args__ = (
        in_check("status", ORGANIZATION_STATUSES, "status"),
        # регистронезависимая уникальность кода организации
        Index("uq_organizations_lower_code", text("lower(code)"), unique=True),
    )


class OrganizationSetting(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Настройки организации как key/value — чтобы не плодить колонки."""

    __tablename__ = "organization_settings"

    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="settings")

    __table_args__ = (
        Index("uq_organization_settings_org_key", "organization_id", "key", unique=True),
    )
