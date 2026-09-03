"""Привязка Telegram-аккаунта к сотруднику.

Именно отсюда бот узнаёт, КТО сканирует QR: сотрудник определяется по
привязанному Telegram-аккаунту, а не по данным, присланным клиентом.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import TELEGRAM_ACCOUNT_STATUSES, in_check

if TYPE_CHECKING:
    from src.modules.employees.models import Employee


class TelegramAccount(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    __tablename__ = "telegram_accounts"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_code: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="ru"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_interaction_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    employee: Mapped["Employee"] = relationship(back_populates="telegram_account")

    __table_args__ = (
        # один сотрудник — один Telegram, один Telegram — один сотрудник
        UniqueConstraint("employee_id", name="uq_telegram_accounts_employee"),
        UniqueConstraint("telegram_user_id", name="uq_telegram_accounts_tg_user"),
        in_check("status", TELEGRAM_ACCOUNT_STATUSES, "status"),
    )
