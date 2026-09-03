"""Аудит изменений. Таблица неизменяемая: только INSERT, никаких UPDATE/DELETE.

`entity_id` — полиморфная ссылка на любую сущность системы, поэтому обычного
внешнего ключа у неё нет и быть не может. Ссылочную целостность здесь заменяет
пара (entity_type, entity_id).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import (
    Base,
    OrganizationScopedMixin,
    UUIDPrimaryKeyMixin,
)


class AuditLog(UUIDPrimaryKeyMixin, OrganizationScopedMixin, Base):
    __tablename__ = "audit_logs"

    # админа можно заблокировать или удалить — запись аудита обязана остаться
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    old_values: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_values: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id",
              text("occurred_at DESC")),
        Index("ix_audit_logs_actor_user", "actor_user_id",
              text("occurred_at DESC")),
        Index("ix_audit_logs_org_time", "organization_id",
              text("occurred_at DESC")),
    )
