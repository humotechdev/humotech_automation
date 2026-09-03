"""Роли, разрешения и области видимости.

Права HR определяются парой «роль + область». Область — это организация целиком,
регион или один офис:

    region_id IS NULL и office_id IS NULL  -> вся организация
    указан region_id                        -> весь регион
    указан office_id                        -> только этот офис
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    Base,
    CreatedAtMixin,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from src.modules.users.models import User


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """organization_id NULL — системная роль, общая для всех организаций."""

    __tablename__ = "roles"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # системные роли (organization_id IS NULL) уникальны глобально...
        Index(
            "uq_roles_system_code",
            "code",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
        # ...а роли организации — внутри своей организации
        Index(
            "uq_roles_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
    )


class Permission(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Справочник разрешений. Общий для всех организаций, меняется миграциями."""

    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RolePermission(CreatedAtMixin, Base):
    """Связка роль-разрешение. Чисто техническая таблица, отсюда CASCADE."""

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )

    role: Mapped["Role"] = relationship(back_populates="permissions")
    permission: Mapped["Permission"] = relationship()


class UserRoleScope(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, CreatedAtMixin, Base
):
    """Какая роль и на какой территории действует у пользователя."""

    __tablename__ = "user_role_scopes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id", ondelete="RESTRICT"), nullable=True
    )
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="RESTRICT"), nullable=True
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="role_scopes")
    role: Mapped["Role"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "user_id", "role_id", "region_id", "office_id", "valid_from",
            name="uq_user_role_scopes_grant",
        ),
        # область задаётся ЛИБО регионом, ЛИБО офисом, но не обоими сразу
        CheckConstraint(
            "region_id IS NULL OR office_id IS NULL", name="scope_not_both"
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="valid_period"
        ),
    )
