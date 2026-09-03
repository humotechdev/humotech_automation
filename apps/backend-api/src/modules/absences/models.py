"""Больничные, отпуска и прочие отсутствия.

Разделение намеренное:
  * `absence_requests`   — то, что ПОПРОСИЛ сотрудник (в любом статусе,
                           включая отклонённые и черновики);
  * `employee_absences`  — то, что РЕАЛЬНО подтверждено и учитывается
                           в отчётах и балансах.

Отклонённая заявка не создаёт периода отсутствия.
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
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
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
from src.core.database.enums import (
    ABSENCE_ACTIONS,
    ABSENCE_REQUEST_KINDS,
    ABSENCE_REQUEST_STATUSES,
    DOCUMENT_VERIFICATION_STATUSES,
    EMPLOYEE_ABSENCE_STATUSES,
    in_check,
)

if TYPE_CHECKING:
    from src.modules.employees.models import Employee


class AbsenceType(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """Справочник типов отсутствия: больничный, отпуск, командировка и т.д."""

    __tablename__ = "absence_types"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_paid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    requires_document: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # например: больничный до 3 дней без справки, дальше документ обязателен
    document_required_after_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    deducts_leave_balance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_absence_types_org_code"),
        CheckConstraint(
            "document_required_after_days IS NULL OR document_required_after_days >= 0",
            name="document_days_non_negative",
        ),
    )


class AbsenceRequest(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Заявка сотрудника. Продление и отмена — тоже заявки, через parent_request_id."""

    __tablename__ = "absence_requests"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    absence_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("absence_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("absence_requests.id", ondelete="RESTRICT"),
        nullable=True,
    )
    request_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    employee_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    employee: Mapped["Employee"] = relationship()
    documents: Mapped[list["AbsenceDocument"]] = relationship(
        back_populates="request"
    )
    actions: Mapped[list["AbsenceAction"]] = relationship(back_populates="request")

    __table_args__ = (
        in_check("request_kind", ABSENCE_REQUEST_KINDS, "request_kind"),
        in_check("status", ABSENCE_REQUEST_STATUSES, "status"),
        CheckConstraint(
            "requested_end_at IS NULL OR requested_start_at IS NULL "
            "OR requested_end_at >= requested_start_at",
            name="end_after_start",
        ),
        CheckConstraint(
            "parent_request_id IS NULL OR parent_request_id <> id",
            name="no_self_parent",
        ),
        # продление и отмена всегда ссылаются на исходную заявку
        CheckConstraint(
            "request_kind = 'CREATE' OR parent_request_id IS NOT NULL",
            name="derived_needs_parent",
        ),
        Index("ix_absence_requests_status", "organization_id", "status"),
    )


class EmployeeAbsence(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Подтверждённый период отсутствия. Только то, что реально согласовано."""

    __tablename__ = "employee_absences"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    absence_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("absence_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    origin_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("absence_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    employee: Mapped["Employee"] = relationship()

    __table_args__ = (
        in_check("status", EMPLOYEE_ABSENCE_STATUSES, "status"),
        CheckConstraint("end_at >= start_at", name="end_after_start"),
        Index("ix_employee_absences_period", "employee_id", "start_at", "end_at"),
    )


class AbsenceDocument(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Справка или иной документ, приложенный к заявке."""

    __tablename__ = "absence_documents"

    absence_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("absence_requests.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="RESTRICT"), nullable=False
    )
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False)
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verification_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped["AbsenceRequest"] = relationship(back_populates="documents")

    __table_args__ = (
        in_check(
            "verification_status",
            DOCUMENT_VERIFICATION_STATUSES,
            "verification_status",
        ),
        UniqueConstraint(
            "absence_request_id", "file_id", name="uq_absence_documents_file"
        ),
    )


class AbsenceAction(UUIDPrimaryKeyMixin, OrganizationScopedMixin, CreatedAtMixin, Base):
    """Неизменяемая история действий по заявке: кто, что и когда сделал."""

    __tablename__ = "absence_actions"

    absence_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("absence_requests.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    # автором может быть и админ (user), и сам сотрудник (employee)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped["AbsenceRequest"] = relationship(back_populates="actions")

    __table_args__ = (in_check("action", ABSENCE_ACTIONS, "action"),)


class LeaveBalance(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """Баланс дней по типу отсутствия за год. Хранится в минутах.

    Доступно = allocated + adjustment - used - reserved.
    `reserved_minutes` — то, что уже занято поданной, но ещё не решённой заявкой.
    """

    __tablename__ = "leave_balances"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    absence_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("absence_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    allocated_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    reserved_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    used_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    # ручная корректировка HR, может быть отрицательной
    adjustment_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "absence_type_id", "year", name="uq_leave_balances_year"
        ),
        CheckConstraint("year BETWEEN 2000 AND 2200", name="year_range"),
        CheckConstraint("allocated_minutes >= 0", name="allocated_non_negative"),
        CheckConstraint("reserved_minutes >= 0", name="reserved_non_negative"),
        CheckConstraint("used_minutes >= 0", name="used_non_negative"),
    )
