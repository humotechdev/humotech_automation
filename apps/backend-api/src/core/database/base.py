"""Базовый класс моделей и общие миксины.

Правила, зашитые здесь и обязательные для всех таблиц:
  * первичный ключ — UUID, генерирует сам PostgreSQL (`gen_random_uuid()`);
  * все временные метки — TIMESTAMPTZ, значения хранятся в UTC;
  * имена ограничений и индексов задаёт единая конвенция, иначе Alembic будет
    генерировать безымянные constraint'ы, которые потом невозможно изменить.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow_default() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class UUIDPrimaryKeyMixin:
    """id UUID PRIMARY KEY DEFAULT gen_random_uuid()"""

    @declared_attr
    def id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        )


class CreatedAtMixin:
    """Только created_at — для неизменяемых событий и журналов."""

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True), nullable=False, server_default=text("now()")
        )


class TimestampMixin(CreatedAtMixin):
    """created_at + updated_at — для обычных изменяемых таблиц."""

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
            onupdate=text("now()"),
        )


class ArchivableMixin:
    """archived_at — вместо физического удаления."""

    @declared_attr
    def archived_at(cls) -> Mapped[datetime | None]:
        return mapped_column(DateTime(timezone=True), nullable=True)


class OrganizationScopedMixin:
    """organization_id на большинстве таблиц: изоляция данных между организациями."""

    @declared_attr
    def organization_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
