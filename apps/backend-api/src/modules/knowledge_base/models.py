"""База знаний: то, что курирует HR, и то, из чего строится ответ ассистента.

Раскладка:
  * `knowledge_sources` — версионируемый документ: правило, инструкция, FAQ.
    Одна строка = одна ВЕРСИЯ. Активной в цепочке версий может быть только одна.
  * `knowledge_chunks`  — куски текста источника с эмбеддингами, по ним идёт поиск.
  * `faq_entries`       — утверждённые пары «вопрос — ответ». Точное попадание
                          сюда отдаётся сотруднику БЕЗ обращения к LLM.
  * `knowledge_index_jobs` — очередь фоновой индексации. Отдельного брокера нет:
                          таблица и есть очередь, воркер забирает задачи через
                          SELECT ... FOR UPDATE SKIP LOCKED.

Область действия источника задаётся тройкой office_id / region_id / department_id:
оба NULL — правило глобальное. Приоритет при совпадении: офис > регион > глобальное.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    ArchivableMixin,
    Base,
    CreatedAtMixin,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import (
    FAQ_ENTRY_STATUSES,
    INDEX_JOB_STATUSES,
    KNOWLEDGE_SOURCE_STATUSES,
    KNOWLEDGE_SOURCE_TYPES,
    in_check,
)

# Размерность text-embedding-3-small. Зашита в тип колонки, поэтому смена
# embedding-модели на модель другой размерности потребует миграции.
EMBEDDING_DIMENSIONS = 1536

if TYPE_CHECKING:
    pass


class KnowledgeSource(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, ArchivableMixin, Base
):
    """Одна версия одного документа базы знаний.

    Версии связаны через `parent_source_id`: у первой версии он NULL, у каждой
    следующей указывает на предыдущую. Активной в цепочке может быть только одна
    строка со статусом ACTIVE — это гарантирует частичный уникальный индекс.
    """

    __tablename__ = "knowledge_sources"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)

    # область действия; NULL + NULL = правило для всей организации
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256 нормализованного текста: если содержание не изменилось,
    # эмбеддинги пересчитывать не нужно
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Явный приоритет внутри одного уровня области действия. Если два разных
    # правила одного уровня подходят под вопрос и приоритеты равны — модель
    # не выбирает сама, вопрос уходит в ESCALATED к HR.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    parent_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="RESTRICT"),
        nullable=True,
    )

    meta: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    __table_args__ = (
        in_check("source_type", KNOWLEDGE_SOURCE_TYPES, "source_type"),
        in_check("status", KNOWLEDGE_SOURCE_STATUSES, "status"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("char_length(content_hash) = 64", name="content_hash_length"),
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL "
            "OR effective_to >= effective_from",
            name="effective_period",
        ),
        # правило нельзя привязать одновременно к офису и к региону:
        # уровень области действия должен быть однозначным
        CheckConstraint(
            "office_id IS NULL OR region_id IS NULL", name="scope_not_both"
        ),
        CheckConstraint(
            "parent_source_id IS NULL OR parent_source_id <> id", name="no_self_parent"
        ),
        # ACTIVE-версия в цепочке ровно одна
        Index(
            "uq_knowledge_sources_active_lineage",
            "organization_id", "title", "language",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_knowledge_sources_lookup", "organization_id", "status", "language"),
        # полнотекстовый поиск как вторая половина гибридного retrieval
        Index(
            "ix_knowledge_sources_fts",
            text("to_tsvector('simple', title || ' ' || content)"),
            postgresql_using="gin",
        ),
    )


class KnowledgeChunk(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, CreatedAtMixin, Base
):
    """Кусок текста источника с эмбеддингом. По нему идёт векторный поиск."""

    __tablename__ = "knowledge_chunks"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    source: Mapped["KnowledgeSource"] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("source_id", "chunk_index", name="uq_knowledge_chunks_index"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint("token_count > 0", name="token_count_positive"),
        # HNSW, а не ivfflat: ivfflat строит списки по уже существующим строкам
        # и на пустой таблице бесполезен, а стартуем мы именно с пустой.
        Index(
            "ix_knowledge_chunks_embedding_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_knowledge_chunks_fts",
            text("to_tsvector('simple', chunk_text)"),
            postgresql_using="gin",
        ),
    )


class FaqEntry(UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base):
    """Утверждённая пара «вопрос — ответ».

    Точное попадание сюда отдаётся сотруднику дословно, без обращения к LLM:
    самый дешёвый, быстрый и предсказуемый путь ответа.
    """

    __tablename__ = "faq_entries"

    canonical_question: Mapped[str] = mapped_column(Text, nullable=False)
    approved_answer: Mapped[str] = mapped_column(Text, nullable=False)
    question_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="RESTRICT"),
        nullable=True,
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="RESTRICT"), nullable=True
    )
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        in_check("status", FAQ_ENTRY_STATUSES, "status"),
        CheckConstraint("office_id IS NULL OR region_id IS NULL", name="scope_not_both"),
        CheckConstraint("char_length(content_hash) = 64", name="content_hash_length"),
        Index("ix_faq_entries_lookup", "organization_id", "status", "language"),
        Index(
            "ix_faq_entries_embedding_cosine",
            "question_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"question_embedding": "vector_cosine_ops"},
        ),
    )


class KnowledgeIndexJob(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, CreatedAtMixin, Base
):
    """Очередь индексации. Брокера нет — очередью служит сама таблица.

    Воркер забирает задачи через SELECT ... FOR UPDATE SKIP LOCKED, поэтому
    несколько воркеров могут работать параллельно без внешней координации.
    """

    __tablename__ = "knowledge_index_jobs"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # экспоненциальный backoff между попытками
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        in_check("status", INDEX_JOB_STATUSES, "status"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # очередь: воркер выбирает QUEUED, у которых подошло время попытки
        Index(
            "ix_knowledge_index_jobs_queue",
            "next_attempt_at",
            postgresql_where=text("status = 'QUEUED'"),
        ),
    )
