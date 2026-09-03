"""База знаний AI-ассистента: источники, чанки, FAQ и очередь индексации.

Публикация двухфазная: DRAFT -> INDEXING -> ACTIVE. Пока новая версия
индексируется, сотрудникам продолжает отвечать предыдущая ACTIVE-версия,
поэтому уникальность «одна активная версия на заголовок» — частичный индекс,
а не обычное ограничение.

Полнотекстовые GIN-индексы по `to_tsvector` здесь НЕ объявлены: Django строит
их через `SearchVector`, который оборачивает колонки в `COALESCE(...)`, а это
другое поведение на NULL. Они создаются точным SQL в миграции
`core/0002_raw_schema_objects`.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from pgvector.django import HnswIndex, VectorField

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    FAQ_ENTRY_STATUSES,
    INDEX_JOB_STATUSES,
    KNOWLEDGE_SOURCE_STATUSES,
    KNOWLEDGE_SOURCE_TYPES,
    choices,
    status_check,
)
from humotech.core.models import (
    CreatedAtModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)

EMBEDDING_DIMENSIONS = 1536


class KnowledgeSource(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Документ базы знаний в конкретной версии."""

    title = models.CharField(max_length=255)
    source_type = models.CharField(
        max_length=20, choices=choices(KNOWLEDGE_SOURCE_TYPES)
    )
    language = models.CharField(max_length=10)
    content = models.TextField()
    content_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20, choices=choices(KNOWLEDGE_SOURCE_STATUSES)
    )
    version = models.IntegerField(db_default=1)
    parent_source = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        db_column="parent_source_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="versions",
    )
    # Область действия правила: офис приоритетнее региона, регион — глобального.
    region = models.ForeignKey(
        "regions.Region",
        on_delete=models.PROTECT,
        db_column="region_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    department = models.ForeignKey(
        "departments.Department",
        on_delete=models.PROTECT,
        db_column="department_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    priority = models.IntegerField(db_default=0)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        db_column="created_by_user_id",
        db_index=False,
        related_name="+",
    )
    approved_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="approved_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    meta = models.JSONField(db_column="metadata", null=True, blank=True)

    class Meta:
        db_table = "knowledge_sources"
        verbose_name = "источник знаний"
        verbose_name_plural = "источники знаний"
        constraints = [
            status_check(
                "status", KNOWLEDGE_SOURCE_STATUSES, "ck_knowledge_sources_status"
            ),
            status_check(
                "source_type", KNOWLEDGE_SOURCE_TYPES,
                "ck_knowledge_sources_source_type",
            ),
            raw_check("version > 0", "ck_knowledge_sources_version_positive"),
            raw_check(
                "char_length(content_hash) = 64",
                "ck_knowledge_sources_content_hash_length",
            ),
            raw_check(
                "office_id IS NULL OR region_id IS NULL",
                "ck_knowledge_sources_scope_not_both",
            ),
            raw_check(
                "effective_to IS NULL OR effective_from IS NULL "
                "OR effective_to >= effective_from",
                "ck_knowledge_sources_effective_period",
            ),
            raw_check(
                "parent_source_id IS NULL OR parent_source_id <> id",
                "ck_knowledge_sources_no_self_parent",
            ),
            # Активная версия одна на заголовок и язык. Частичный индекс, а не
            # ограничение: черновиков и архивных версий может быть сколько угодно.
            models.UniqueConstraint(
                fields=["organization", "title", "language"],
                condition=Q(status="ACTIVE"),
                name="uq_knowledge_sources_active_lineage",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_knowledge_sources_organization_id"
            ),
            models.Index(fields=["region"], name="ix_knowledge_sources_region_id"),
            models.Index(fields=["office"], name="ix_knowledge_sources_office_id"),
            models.Index(
                fields=["organization", "status", "language"],
                name="ix_knowledge_sources_lookup",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} (v{self.version}, {self.status})"


class KnowledgeChunk(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Фрагмент документа с эмбеддингом. Удаляется вместе с источником."""

    source = models.ForeignKey(
        KnowledgeSource,
        on_delete=models.CASCADE,
        db_column="source_id",
        db_index=False,
        related_name="chunks",
    )
    chunk_index = models.IntegerField()
    chunk_text = models.TextField()
    token_count = models.IntegerField()
    content_hash = models.CharField(max_length=64)
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)
    meta = models.JSONField(db_column="metadata", null=True, blank=True)

    class Meta:
        db_table = "knowledge_chunks"
        verbose_name = "фрагмент знаний"
        verbose_name_plural = "фрагменты знаний"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "chunk_index"], name="uq_knowledge_chunks_index"
            ),
            raw_check("chunk_index >= 0", "ck_knowledge_chunks_chunk_index_non_negative"),
            raw_check("token_count > 0", "ck_knowledge_chunks_token_count_positive"),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_knowledge_chunks_organization_id"
            ),
            models.Index(fields=["source"], name="ix_knowledge_chunks_source_id"),
            # HNSW, а не ivfflat: ivfflat строит списки по уже загруженным
            # данным и на пустой таблице бесполезен, а знания заливаются
            # постепенно.
            HnswIndex(
                name="ix_knowledge_chunks_embedding_cosine",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_id} #{self.chunk_index}"


class FaqEntry(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Утверждённая пара «вопрос — ответ». Отвечает без обращения к модели."""

    canonical_question = models.TextField()
    approved_answer = models.TextField()
    language = models.CharField(max_length=10)
    content_hash = models.CharField(max_length=64)
    question_embedding = VectorField(
        dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True
    )
    status = models.CharField(max_length=20, choices=choices(FAQ_ENTRY_STATUSES))
    priority = models.IntegerField(db_default=0)
    region = models.ForeignKey(
        "regions.Region",
        on_delete=models.PROTECT,
        db_column="region_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    source = models.ForeignKey(
        KnowledgeSource,
        on_delete=models.PROTECT,
        db_column="source_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="faq_entries",
    )
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        db_column="created_by_user_id",
        db_index=False,
        related_name="+",
    )
    approved_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="approved_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "faq_entries"
        verbose_name = "запись FAQ"
        verbose_name_plural = "записи FAQ"
        constraints = [
            status_check("status", FAQ_ENTRY_STATUSES, "ck_faq_entries_status"),
            raw_check(
                "char_length(content_hash) = 64", "ck_faq_entries_content_hash_length"
            ),
            raw_check(
                "office_id IS NULL OR region_id IS NULL",
                "ck_faq_entries_scope_not_both",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_faq_entries_organization_id"
            ),
            models.Index(
                fields=["organization", "status", "language"],
                name="ix_faq_entries_lookup",
            ),
            HnswIndex(
                name="ix_faq_entries_embedding_cosine",
                fields=["question_embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return self.canonical_question[:60]


class KnowledgeIndexJob(
    UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel
):
    """Очередь индексации без брокера.

    Обработчик берёт задание через `SELECT ... FOR UPDATE SKIP LOCKED`,
    поэтому нескольким процессам не нужен ни Redis, ни Celery: сама база
    гарантирует, что задание достанется одному.
    """

    source = models.ForeignKey(
        KnowledgeSource,
        on_delete=models.CASCADE,
        db_column="source_id",
        db_index=False,
        related_name="index_jobs",
    )
    status = models.CharField(max_length=20, choices=choices(INDEX_JOB_STATUSES))
    attempts = models.IntegerField(db_default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_summary = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "knowledge_index_jobs"
        verbose_name = "задание индексации"
        verbose_name_plural = "задания индексации"
        constraints = [
            status_check(
                "status", INDEX_JOB_STATUSES, "ck_knowledge_index_jobs_status"
            ),
            raw_check("attempts >= 0", "ck_knowledge_index_jobs_attempts_non_negative"),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_knowledge_index_jobs_organization_id"
            ),
            models.Index(fields=["source"], name="ix_knowledge_index_jobs_source_id"),
            # В индексе лежит только очередь, а не вся история заданий.
            models.Index(
                fields=["next_attempt_at"],
                condition=Q(status="QUEUED"),
                name="ix_knowledge_index_jobs_queue",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_id} ({self.status})"
