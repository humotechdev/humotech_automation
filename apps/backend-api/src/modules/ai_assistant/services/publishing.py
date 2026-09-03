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

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.modules.ai_assistant.errors import PublishingError
from src.modules.ai_assistant.services.chunking import hash_text
from src.modules.knowledge_base.models import (
    KnowledgeChunk,
    KnowledgeIndexJob,
    KnowledgeSource,
)
from src.modules.organizations.models import Organization

logger = logging.getLogger("humotech.ai.publishing")


@dataclass(frozen=True)
class PublishResult:
    source_id: uuid.UUID
    version: int
    archived_source_id: uuid.UUID | None
    knowledge_revision: int


class KnowledgePublishingService:
    def __init__(self, session: Session) -> None:
        self.session = session

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
            parent = self.session.get(KnowledgeSource, parent_source_id)
            if parent is None:
                raise PublishingError("Родительская версия не найдена")
            version = parent.version + 1

        source = KnowledgeSource(
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
        self.session.add(source)
        self.session.flush()
        return source

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
        if "content" in fields and fields["content"]:
            source.content_hash = hash_text(fields["content"])
        source.status = "DRAFT"
        self.session.flush()
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
        job = KnowledgeIndexJob(
            organization_id=source.organization_id,
            source_id=source.id,
            status="QUEUED",
            attempts=0,
            next_attempt_at=datetime.now(tz=timezone.utc),
        )
        self.session.add(job)
        self.session.flush()
        return job

    def mark_indexing_failed(self, source_id: uuid.UUID, error: str) -> None:
        """Неудачная индексация НЕ выключает старую версию."""
        source = self._get(source_id)
        source.status = "ERROR"
        self.session.flush()
        logger.warning("индексация источника %s не удалась: %s", source_id, error)

    # -------------------------------------------------------------- publishing

    def publish(self, source_id: uuid.UUID, *, approved_by_user_id: uuid.UUID) -> PublishResult:
        """Активация новой версии. Всё внутри одной транзакции.

        Вызывающий код обязан выполнять это в транзакции: либо версия
        становится активной вместе с архивацией предыдущей и ростом ревизии,
        либо не происходит ничего.
        """
        source = self._get(source_id)

        indexed = self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.source_id == source.id,
                KnowledgeChunk.embedding.is_not(None),
            )
        )
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
        previous = self.session.scalar(
            select(KnowledgeSource).where(
                KnowledgeSource.organization_id == source.organization_id,
                KnowledgeSource.title == source.title,
                KnowledgeSource.language == source.language,
                KnowledgeSource.status == "ACTIVE",
                KnowledgeSource.id != source.id,
            )
        )
        archived_id = None
        if previous is not None:
            previous.status = "ARCHIVED"
            previous.archived_at = now
            archived_id = previous.id
            # частичный уникальный индекс не позволит двум ACTIVE
            # существовать одновременно, поэтому архивируем до активации
            self.session.flush()

        source.status = "ACTIVE"
        source.published_at = now
        source.approved_by_user_id = approved_by_user_id
        source.approved_at = now

        revision = self._bump_revision(source.organization_id)
        self.session.flush()

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
        revision = self._bump_revision(source.organization_id)
        self.session.flush()
        return revision

    def version_history(self, organization_id: uuid.UUID, title: str, language: str):
        return list(
            self.session.scalars(
                select(KnowledgeSource)
                .where(
                    KnowledgeSource.organization_id == organization_id,
                    KnowledgeSource.title == title,
                    KnowledgeSource.language == language,
                )
                .order_by(KnowledgeSource.version.desc())
            )
        )

    # ----------------------------------------------------------------- helpers

    def _bump_revision(self, organization_id: uuid.UUID) -> int:
        """Инкремент делает СУБД: два одновременных публикатора не потеряют счёт."""
        revision = self.session.execute(
            update(Organization)
            .where(Organization.id == organization_id)
            .values(knowledge_revision=Organization.knowledge_revision + 1)
            .returning(Organization.knowledge_revision)
        ).scalar_one()
        return revision

    def _get(self, source_id: uuid.UUID) -> KnowledgeSource:
        source = self.session.get(KnowledgeSource, source_id)
        if source is None:
            raise PublishingError(f"Источник {source_id} не найден")
        return source


def current_revision(session: Session, organization_id: uuid.UUID) -> int:
    value = session.scalar(
        select(Organization.knowledge_revision).where(
            Organization.id == organization_id
        )
    )
    return int(value or 0)
