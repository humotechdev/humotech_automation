"""Метаданные файлов. Сами файлы лежат в object storage, а не в PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import (
    Base,
    CreatedAtMixin,
    OrganizationScopedMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import FILE_SCAN_STATUSES, in_check


class File(UUIDPrimaryKeyMixin, OrganizationScopedMixin, CreatedAtMixin, Base):
    __tablename__ = "files"

    storage_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # ключ объекта в хранилище (S3 key и т.п.)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    uploaded_by_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # файл нельзя отдавать, пока антивирус не сказал CLEAN
    scan_status: Mapped[str] = mapped_column(String(20), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        in_check("scan_status", FILE_SCAN_STATUSES, "scan_status"),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        CheckConstraint("char_length(checksum_sha256) = 64", name="checksum_length"),
        Index(
            "uq_files_storage_key",
            "storage_provider", "storage_key",
            unique=True,
        ),
    )
