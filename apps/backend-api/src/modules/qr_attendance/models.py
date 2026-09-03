"""Отметки по QR: сырые события и посчитанные рабочие сессии.

Разница между таблицами принципиальная:

  * `attendance_events`   — ЖУРНАЛ. Одно сканирование = одна строка, навсегда.
                            Строки никогда не изменяются и не удаляются.
                            Сюда попадают и отклонённые попытки (REJECTED) —
                            это материал для расследования инцидентов.
  * `attendance_sessions` — ВЫВОД. Интервал «пришёл — ушёл», посчитанный
                            бэкендом из пары событий. Может пересчитываться,
                            корректироваться и признаваться недействительным,
                            но исходные события при этом не трогаются.

Исправление ошибочной отметки делается заявкой
(`attendance_correction_requests`), а не редактированием события.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    Base,
    CreatedAtMixin,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import (
    ATTENDANCE_EVENT_TYPES,
    ATTENDANCE_SESSION_STATUSES,
    ATTENDANCE_SOURCES,
    CORRECTION_REQUEST_STATUSES,
    VERIFICATION_STATUSES,
    in_check,
)

if TYPE_CHECKING:
    from src.modules.employees.models import Employee


class AttendanceEvent(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, CreatedAtMixin, Base
):
    """Неизменяемое событие сканирования. Без updated_at — строка не меняется."""

    __tablename__ = "attendance_events"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # исторический snapshot: офис, определённый сервером через QR-точку в момент
    # события. Если точку потом перенесут в другой офис — история не поедет.
    office_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    qr_point_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("office_qr_points.id", ondelete="RESTRICT"),
        nullable=True,
    )
    qr_display_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("qr_display_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    employee_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employee_devices.id", ondelete="RESTRICT"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    # ставит ТОЛЬКО сервер: клиент не может прислать готовый результат проверки
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False)

    # время события (проверяется сервером на расхождение с серверными часами)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # одноразовость QR: nonce хранится хешем, повтор ловится уникальным индексом
    qr_nonce_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    qr_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    location_accuracy_m: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    inside_geofence: Mapped[bool | None] = mapped_column(nullable=True)
    inside_office_network: Mapped[bool | None] = mapped_column(nullable=True)

    # идемпотентность повторной отправки с клиента при плохой связи
    client_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # `metadata` — занятое имя в SQLAlchemy, поэтому атрибут назван иначе,
    # а колонка в базе называется именно metadata
    event_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    employee: Mapped["Employee"] = relationship()

    __table_args__ = (
        in_check("event_type", ATTENDANCE_EVENT_TYPES, "event_type"),
        in_check("source", ATTENDANCE_SOURCES, "source"),
        in_check("verification_status", VERIFICATION_STATUSES, "verification_status"),
        # отметка по QR обязана ссылаться на QR-точку — иначе офис не определить
        CheckConstraint(
            "source <> 'QR' OR qr_point_id IS NOT NULL", name="qr_requires_point"
        ),
        CheckConstraint(
            "qr_expires_at IS NULL OR qr_issued_at IS NULL "
            "OR qr_expires_at > qr_issued_at",
            name="qr_expiry_after_issue",
        ),
        Index("ix_attendance_events_employee_time", "employee_id",
              text("occurred_at DESC")),
        Index("ix_attendance_events_office_time", "office_id",
              text("occurred_at DESC")),
        Index("ix_attendance_events_qr_point_time", "qr_point_id",
              text("occurred_at DESC")),
        Index("ix_attendance_events_status_time", "verification_status",
              text("occurred_at DESC")),
        # Один nonce — одно УСПЕШНОЕ использование одним сотрудником.
        #
        # Индекс намеренно частичный по verification_status = 'ACCEPTED'.
        # Иначе два требования ТЗ противоречат друг другу: «запрещай повторное
        # использование nonce» и «все отклонённые попытки тоже сохраняй в
        # attendance_events». При сплошном UNIQUE вторая попытка с тем же nonce
        # не смогла бы записаться даже как REJECTED — то есть попытка обмана
        # исчезла бы из журнала, а это ровно то событие, которое нужнее всего.
        # Так nonce остаётся одноразовым, а все попытки его повторить видны.
        Index(
            "uq_attendance_events_nonce",
            "employee_id", "qr_nonce_hash", "event_type",
            unique=True,
            postgresql_where=text(
                "qr_nonce_hash IS NOT NULL AND verification_status = 'ACCEPTED'"
            ),
        ),
        # защита от дублей при ретраях клиента
        Index(
            "uq_attendance_events_client_event",
            "employee_id", "client_event_id",
            unique=True,
            postgresql_where=text("client_event_id IS NOT NULL"),
        ),
    )


class AttendanceSession(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Интервал нахождения в офисе, посчитанный бэкендом из пары событий."""

    __tablename__ = "attendance_sessions"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attendance_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    exit_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attendance_events.id", ondelete="RESTRICT"),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # считает бэкенд, не клиент
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    calculated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    employee: Mapped["Employee"] = relationship()

    __table_args__ = (
        in_check("status", ATTENDANCE_SESSION_STATUSES, "status"),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="end_after_start"
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="duration_non_negative",
        ),
        # закрытая сессия обязана иметь и время выхода, и событие выхода
        CheckConstraint(
            "status <> 'CLOSED' OR (ended_at IS NOT NULL AND exit_event_id IS NOT NULL)",
            name="closed_has_exit",
        ),
        # У сотрудника не может быть двух открытых сессий одновременно.
        Index(
            "uq_attendance_sessions_one_open",
            "employee_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
        Index("ix_attendance_sessions_employee_time", "employee_id",
              text("started_at DESC")),
        Index("ix_attendance_sessions_office_time", "office_id",
              text("started_at DESC")),
    )


class AttendanceCorrectionRequest(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Заявка на исправление отметки. Событие при этом остаётся нетронутым."""

    __tablename__ = "attendance_correction_requests"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    attendance_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attendance_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    requested_entry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_exit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        in_check("status", CORRECTION_REQUEST_STATUSES, "status"),
        CheckConstraint(
            "requested_entry_at IS NOT NULL OR requested_exit_at IS NOT NULL",
            name="something_requested",
        ),
        CheckConstraint(
            "requested_exit_at IS NULL OR requested_entry_at IS NULL "
            "OR requested_exit_at >= requested_entry_at",
            name="exit_after_entry",
        ),
    )
