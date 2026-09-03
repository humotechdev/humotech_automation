"""Сотрудник, история его назначений и доступ к дополнительным офисам."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    ArchivableMixin,
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import (
    EMPLOYMENT_STATUSES,
    EMPLOYMENT_TYPES,
    OFFICE_ACCESS_TYPES,
    WORK_MODES,
    in_check,
)

if TYPE_CHECKING:
    from src.modules.organizations.models import Organization
    from src.modules.telegram.models import TelegramAccount


class Employee(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, ArchivableMixin, Base
):
    """Сотрудник никогда не удаляется физически: у него есть история отметок."""

    __tablename__ = "employees"

    employee_number: Mapped[str] = mapped_column(String(100), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    corporate_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    personal_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    preferred_language: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="ru"
    )
    employment_status: Mapped[str] = mapped_column(String(30), nullable=False)
    # денормализованный флаг для быстрых выборок; источник правды — telegram_accounts
    telegram_connected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    organization: Mapped["Organization"] = relationship(back_populates="employees")
    assignments: Mapped[list["EmployeeAssignment"]] = relationship(
        back_populates="employee",
        foreign_keys="EmployeeAssignment.employee_id",
    )
    office_access: Mapped[list["EmployeeOfficeAccess"]] = relationship(
        back_populates="employee"
    )
    telegram_account: Mapped["TelegramAccount | None"] = relationship(
        back_populates="employee"
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "employee_number", name="uq_employees_org_number"
        ),
        in_check("employment_status", EMPLOYMENT_STATUSES, "employment_status"),
        CheckConstraint(
            "termination_date IS NULL OR termination_date >= hire_date",
            name="termination_after_hire",
        ),
        Index("ix_employees_org_status", "organization_id", "employment_status"),
    )


class EmployeeAssignment(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """История переводов: офис, отдел, должность, руководитель на период.

    Текущее назначение — то, у которого valid_to IS NULL либо период включает
    сегодняшнюю дату. Основное назначение (is_primary) определяет офис сотрудника
    по умолчанию для QR-отметки.
    """

    __tablename__ = "employee_assignments"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    manager_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    employment_type: Mapped[str] = mapped_column(String(30), nullable=False)
    work_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    employee: Mapped["Employee"] = relationship(
        back_populates="assignments", foreign_keys=[employee_id]
    )
    manager: Mapped["Employee | None"] = relationship(
        foreign_keys=[manager_employee_id]
    )

    __table_args__ = (
        in_check("employment_type", EMPLOYMENT_TYPES, "employment_type"),
        in_check("work_mode", WORK_MODES, "work_mode"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="valid_period"
        ),
        CheckConstraint(
            "manager_employee_id IS NULL OR manager_employee_id <> employee_id",
            name="no_self_manager",
        ),
        # У сотрудника не может быть двух пересекающихся ОСНОВНЫХ назначений.
        # Требует расширения btree_gist (создаётся в первой миграции).
        ExcludeConstraint(
            ("employee_id", "="),
            (literal_column("daterange(valid_from, valid_to, '[]')"), "&&"),
            name="ex_employee_assignments_primary_overlap",
            using="gist",
            where=text("is_primary"),
        ),
    )


class EmployeeOfficeAccess(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Право отмечаться в дополнительном офисе, помимо основного назначения."""

    __tablename__ = "employee_office_access"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    access_type: Mapped[str] = mapped_column(String(30), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # админа могут удалить — история доступа от этого исчезать не должна
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    employee: Mapped["Employee"] = relationship(back_populates="office_access")

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "office_id", "valid_from",
            name="uq_employee_office_access_period",
        ),
        in_check("access_type", OFFICE_ACCESS_TYPES, "access_type"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="valid_period"
        ),
    )
