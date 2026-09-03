"""Рабочие графики, дни недели, перерывы, назначение графика и календарь."""

from __future__ import annotations

import uuid
from datetime import date, time
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
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
    CALENDAR_EXCEPTION_TYPES,
    SCHEDULE_STATUSES,
    in_check,
)


class WorkSchedule(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, ArchivableMixin, Base
):
    __tablename__ = "work_schedules"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    weekly_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    late_grace_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    early_leave_grace_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_flexible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    days: Mapped[list["ScheduleDay"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )

    __table_args__ = (
        in_check("status", SCHEDULE_STATUSES, "status"),
        CheckConstraint("weekly_minutes > 0", name="weekly_minutes_positive"),
        CheckConstraint("late_grace_minutes >= 0", name="late_grace_non_negative"),
        CheckConstraint(
            "early_leave_grace_minutes >= 0", name="early_grace_non_negative"
        ),
    )


class ScheduleDay(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """День недели в графике. 1 = понедельник, 7 = воскресенье (ISO-8601)."""

    __tablename__ = "schedule_days"

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_schedules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_working_day: Mapped[bool] = mapped_column(Boolean, nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # ночная смена: конец приходится на следующие сутки
    crosses_midnight: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    schedule: Mapped["WorkSchedule"] = relationship(back_populates="days")
    breaks: Mapped[list["ScheduleBreak"]] = relationship(
        back_populates="schedule_day", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("schedule_id", "weekday", name="uq_schedule_days_weekday"),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday_range"),
        # у рабочего дня время начала и конца обязательно
        CheckConstraint(
            "NOT is_working_day OR (start_time IS NOT NULL AND end_time IS NOT NULL)",
            name="working_day_has_time",
        ),
    )


class ScheduleBreak(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "schedule_breaks"

    schedule_day_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schedule_days.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_paid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    schedule_day: Mapped["ScheduleDay"] = relationship(back_populates="breaks")


class EmployeeScheduleAssignment(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Какой график действует у сотрудника и с какой даты."""

    __tablename__ = "employee_schedule_assignments"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_schedules.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="valid_period"
        ),
        # У сотрудника не может быть двух пересекающихся графиков одновременно.
        ExcludeConstraint(
            ("employee_id", "="),
            (literal_column("daterange(valid_from, valid_to, '[]')"), "&&"),
            name="ex_employee_schedule_assignments_overlap",
            using="gist",
        ),
    )


class CalendarException(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Праздники, сокращённые дни, рабочие выходные.

    office_id NULL — исключение действует на всю организацию.
    """

    __tablename__ = "calendar_exceptions"

    office_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    exception_type: Mapped[str] = mapped_column(String(30), nullable=False)
    is_working_day: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        in_check("exception_type", CALENDAR_EXCEPTION_TYPES, "exception_type"),
        # одна дата — одно исключение: отдельно для офиса и отдельно для всей организации
        Index(
            "uq_calendar_exceptions_office_date",
            "office_id", "date",
            unique=True,
            postgresql_where=text("office_id IS NOT NULL"),
        ),
        Index(
            "uq_calendar_exceptions_org_date",
            "organization_id", "date",
            unique=True,
            postgresql_where=text("office_id IS NULL"),
        ),
    )
