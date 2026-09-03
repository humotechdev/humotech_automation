"""Отдел внутри офиса. Поддерживает вложенность через parent_department_id."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    ArchivableMixin,
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import DEPARTMENT_STATUSES, in_check

if TYPE_CHECKING:
    from src.modules.offices.models import Office


class Department(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, ArchivableMixin, Base
):
    __tablename__ = "departments"

    office_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parent_department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    office: Mapped["Office"] = relationship(back_populates="departments")
    parent: Mapped["Department | None"] = relationship(
        remote_side="Department.id", back_populates="children"
    )
    children: Mapped[list["Department"]] = relationship(back_populates="parent")

    __table_args__ = (
        UniqueConstraint("office_id", "code", name="uq_departments_office_code"),
        in_check("status", DEPARTMENT_STATUSES, "status"),
        CheckConstraint(
            "parent_department_id IS NULL OR parent_department_id <> id",
            name="no_self_parent",
        ),
    )
