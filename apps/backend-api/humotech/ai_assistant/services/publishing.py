"""Двухфазная публикация знаний.

Смысл двух фаз: пока новая версия не проиндексирована, сотрудникам продолжает
отвечать СТАРАЯ действующая версия. Ошибка индексации не должна оставить
компанию без базы знаний.

Порядок:
    1. HR создаёт DRAFT.
    2. Бэкенд проверяет данные и права (права — на уровне use case).
    3. Ставится задача в knowledge_index_jobs, источник -> INDEXING.
    4. Фоновый воркер режет текст на куски и считает эмбеддинги.
    5. Успех -> задача SUCCEEDED, источник готов к активации.
       Неуспех -> источник ERROR, СТАРАЯ версия продолжает работать.
    6. Активация в ОДНОЙ транзакции: новая -> ACTIVE, старая -> ARCHIVED,
       knowledge_revision += 1.
    7. Рост ревизии автоматически обесценивает весь прежний кэш.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from django.db.models import F

from humotech.ai_assistant.errors import PublishingError
from humotech.ai_assistant.services.chunking import hash_text
from humotech.knowledge.models import (
    KnowledgeChunk,
    KnowledgeIndexJob,
    KnowledgeSource,
)
from humotech.organizations.models import Organization

logger = logging.getLogger("humotech.ai.publishing")


@dataclass(frozen=True)
class PublishResult:
    source_id: uuid.UUID
    version: int
    archived_source_id: uuid.UUID | None
    knowledge_revision: int


class KnowledgePublishingService:
    # ------------------------------------------------------------------ draft

    def create_draft(
        self,
        *,
        organization_id: uuid.UUID,
        title: str,
        source_type: str,
        language: str,
        content: str,
        created_by_user_id: uuid.UUID,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        effective_from=None,
        effective_to=None,
        priority: int = 0,
        metadata: dict | None = None,
        parent_source_id: uuid.UUID | None = None,
    ) -> KnowledgeSource:
        if office_id is not None and region_id is not None:
            raise PublishingError(
                "Правило нельзя привязать одновременно к офису и к региону: "
                "уровень области действия должен быть однозначным"
            )
        if not content or not content.strip():
            raise PublishingError("Пустой документ индексировать нечем")

        version = 1
        if parent_source_id is not None:
            parent = KnowledgeSource.objects.filter(id=parent_source_id).first()
            if parent is None:
                raise PublishingError("Родительская версия не найдена")
            version = parent.version + 1

        return KnowledgeSource.objects.create(
            organization_id=organization_id,
            title=title,
            source_type=source_type,
            language=language,
            content=content,
            content_hash=hash_text(content),
            office_id=office_id,
            region_id=region_id,
            department_id=department_id,
            effective_from=effective_from,
            effective_to=effective_to,
            priority=priority,
            status="DRAFT",
            version=version,
            created_by_user_id=created_by_user_id,
            parent_source_id=parent_source_id,
            meta=metadata,
        )

    def update_draft(self, source_id: uuid.UUID, **fields) -> KnowledgeSource:
        source = self._get(source_id)
        if source.status not in ("DRAFT", "ERROR"):
            raise PublishingError(
                f"Редактировать можно только черновик, текущий статус {source.status}"
            )
        for key, value in fields.items():
            if value is None or not hasattr(source, key):
                continue
            setattr(source, key, value)
        if fields.get("content"):
            source.content_hash = hash_text(fields["content"])
        source.status = "DRAFT"
        source.save()
        return source

    # ---------------------------------------------------------------- indexing

    def enqueue_indexing(self, source_id: uuid.UUID) -> KnowledgeIndexJob:
        """Ставит задачу и переводит источник в INDEXING.

        Действующая ACTIVE-версия при этом не трогается.
        """
        source = self._get(source_id)
        if source.status == "ACTIVE":
            raise PublishingError(
                "Действующую версию переиндексировать нельзя: создайте новую"
            )

        source.status = "INDEXING"
        source.save(update_fields=["status", "updated_at"])
        return KnowledgeIndexJob.objects.create(
            organization_id=source.organization_id,
            source=source,
            status="QUEUED",
            attempts=0,
            next_attempt_at=datetime.now(tz=timezone.utc),
        )

    def mark_indexing_failed(self, source_id: uuid.UUID, error: str) -> None:
        """Неудачная индексация НЕ выключает старую версию."""
        source = self._get(source_id)
        source.status = "ERROR"
        source.save(update_fields=["status", "updated_at"])
        logger.warning("индексация источника %s не удалась: %s", source_id, error)

    # -------------------------------------------------------------- publishing

    def publish(
        self, source_id: uuid.UUID, *, approved_by_user_id: uuid.UUID
    ) -> PublishResult:
        """Активация новой версии. Всё внутри одной транзакции.

        Вызывающий код обязан выполнять это в транзакции: либо версия
        становится активной вместе с архивацией предыдущей и ростом ревизии,
        либо не происходит ничего.
        """
        source = self._get(source_id)

        indexed = KnowledgeChunk.objects.filter(
            source_id=source.id, embedding__isnull=False
        ).exists()
        if not indexed:
            raise PublishingError(
                "Публиковать нечего: у версии нет проиндексированных кусков. "
                "Сначала дождитесь успешной индексации."
            )
        if source.status not in ("INDEXING", "DRAFT"):
            raise PublishingError(
                f"Публиковать можно версию в статусе INDEXING или DRAFT, "
                f"текущий статус {source.status}"
            )

        now = datetime.now(tz=timezone.utc)

        # предыдущая действующая версия того же документа
        previous = (
            KnowledgeSource.objects.filter(
                organization_id=source.organization_id,
                title=source.title,
                language=source.language,
                status="ACTIVE",
            )
            .exclude(id=source.id)
            .first()
        )
        archived_id = None
        if previous is not None:
            previous.status = "ARCHIVED"
            previous.archived_at = now
            # частичный уникальный индекс не позволит двум ACTIVE
            # существовать одновременно, поэтому архивируем ДО активации
            previous.save(update_fields=["status", "archived_at", "updated_at"])
            archived_id = previous.id

        source.status = "ACTIVE"
        source.published_at = now
        source.approved_by_user_id = approved_by_user_id
        source.approved_at = now
        source.save()

        revision = self._bump_revision(source.organization_id)

        logger.info(
            "источник %s опубликован как версия %s, ревизия базы знаний %s",
            source.id, source.version, revision,
        )
        return PublishResult(
            source_id=source.id,
            version=source.version,
            archived_source_id=archived_id,
            knowledge_revision=revision,
        )

    def archive(self, source_id: uuid.UUID) -> int:
        """Снятие версии с публикации. Тоже увеличивает ревизию."""
        source = self._get(source_id)
        if source.status != "ACTIVE":
            raise PublishingError("Архивировать можно только действующую версию")
        source.status = "ARCHIVED"
        source.archived_at = datetime.now(tz=timezone.utc)
        source.save(update_fields=["status", "archived_at", "updated_at"])
        return self._bump_revision(source.organization_id)

    def version_history(
        self, organization_id: uuid.UUID, title: str, language: str
    ) -> list[KnowledgeSource]:
        return list(
            KnowledgeSource.objects.filter(
                organization_id=organization_id, title=title, language=language
            ).order_by("-version")
        )

    # ----------------------------------------------------------------- helpers

    def _bump_revision(self, organization_id: uuid.UUID) -> int:
        """Инкремент делает СУБД: два одновременных публикатора не потеряют счёт.

        `F("knowledge_revision") + 1` превращается в `SET x = x + 1` на стороне
        PostgreSQL. Прочитать значение, прибавить в Python и записать обратно
        было бы гонкой: две публикации подряд дали бы +1 вместо +2, и часть
        кэша осталась бы действующей после смены правил.
        """
        Organization.objects.filter(id=organization_id).update(
            knowledge_revision=F("knowledge_revision") + 1
        )
        return current_revision(organization_id)

    def _get(self, source_id: uuid.UUID) -> KnowledgeSource:
        source = KnowledgeSource.objects.filter(id=source_id).first()
        if source is None:
            raise PublishingError(f"Источник {source_id} не найден")
        return source


def current_revision(organization_id: uuid.UUID) -> int:
    value = (
        Organization.objects.filter(id=organization_id)
        .values_list("knowledge_revision", flat=True)
        .first()
    )
    return int(value or 0)
