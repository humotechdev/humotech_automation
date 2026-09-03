"""Учётная запись для входа в CRM: HR, администраторы, руководители, техадмины.

Отдельной таблицы `admins` нет и не должно быть: администратор — это
users + roles + user_role_scopes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    ArchivableMixin,
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import USER_STATUSES, in_check

if TYPE_CHECKING:
    from src.modules.organizations.models import Organization
    from src.modules.roles.models import UserRoleScope


class User(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, ArchivableMixin, Base
):
    __tablename__ = "users"

    # NULL — техническая учётка без сотрудника (интеграция, суперадмин вендора)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    # только хеш (argon2/bcrypt). Открытый пароль не хранится нигде и никогда.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="users")
    role_scopes: Mapped[list["UserRoleScope"]] = relationship(back_populates="user")

    __table_args__ = (
        in_check("status", USER_STATUSES, "status"),
        # email уникален внутри организации, без учёта регистра
        Index(
            "uq_users_org_lower_email",
            "organization_id",
            text("lower(email)"),
            unique=True,
        ),
        # один сотрудник — не более одной учётной записи
        Index(
            "uq_users_employee_id",
            "employee_id",
            unique=True,
            postgresql_where=text("employee_id IS NOT NULL"),
        ),
    )
