"""Индексация источника: текст -> куски -> эмбеддинги -> knowledge_chunks.

Ключевая экономия — `content_hash`. Если текст куска не изменился, его вектор
уже посчитан: платить провайдеру за повторный эмбеддинг того же абзаца
не нужно. При правке одного абзаца в длинном документе пересчитывается
именно он, а не весь документ.

Индексация никогда не трогает действующую ACTIVE-версию: она работает
с новой строкой источника, и только успешная индексация даёт право
на публикацию.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.ai_assistant.config import AiSettings, ai_settings
from src.modules.ai_assistant.errors import ConfigurationError
from src.modules.ai_assistant.providers.base import EmbeddingProvider
from src.modules.ai_assistant.services.chunking import split_text
from src.modules.knowledge_base.models import KnowledgeChunk, KnowledgeSource

logger = logging.getLogger("humotech.ai.ingestion")


@dataclass(frozen=True)
class IngestionResult:
    source_id: uuid.UUID
    chunks_total: int
    chunks_embedded: int
    chunks_reused: int
    embedding_model: str
    input_tokens: int


class KnowledgeIngestionService:
    def __init__(
        self,
        session: Session,
        embedding_provider: EmbeddingProvider,
        settings: AiSettings | None = None,
    ) -> None:
        self.session = session
        self.embeddings = embedding_provider
        self.settings = settings or ai_settings

    def index_source(self, source_id: uuid.UUID) -> IngestionResult:
        source = self.session.get(KnowledgeSource, source_id)
        if source is None:
            raise ConfigurationError(f"Источник {source_id} не найден")

        model = self.settings.openai_embedding_model
        if not model.strip():
            raise ConfigurationError("OPENAI_EMBEDDING_MODEL не задан")

        chunks = split_text(source.content)
        if not chunks:
            raise ConfigurationError(
                f"Источник {source_id} пуст: индексировать нечего"
            )

        # что уже посчитано ранее — переиспользуем по хешу содержимого
        known: dict[str, list[float]] = {}
        for chunk_hash, embedding in self.session.execute(
            select(KnowledgeChunk.content_hash, KnowledgeChunk.embedding).where(
                KnowledgeChunk.organization_id == source.organization_id,
                KnowledgeChunk.content_hash.in_([c.content_hash for c in chunks]),
                KnowledgeChunk.embedding.is_not(None),
            )
        ).all():
            known.setdefault(chunk_hash, embedding)

        to_embed = [c for c in chunks if c.content_hash not in known]
        input_tokens = 0
        if to_embed:
            result = self.embeddings.embed(
                [c.text for c in to_embed], model=model
            )
            input_tokens = result.input_tokens
            for chunk, vector in zip(to_embed, result.vectors, strict=True):
                known[chunk.content_hash] = vector

        # старые куски этой версии удаляем: переиндексация всегда полная
        # в рамках одной версии источника
        for existing in self.session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)
        ):
            self.session.delete(existing)
        self.session.flush()

        for chunk in chunks:
            self.session.add(
                KnowledgeChunk(
                    organization_id=source.organization_id,
                    source_id=source.id,
                    chunk_index=chunk.index,
                    chunk_text=chunk.text,
                    embedding=known[chunk.content_hash],
                    token_count=chunk.token_count,
                    content_hash=chunk.content_hash,
                    meta={"source_type": source.source_type},
                )
            )
        self.session.flush()

        logger.info(
            "источник %s проиндексирован: кусков %s, новых эмбеддингов %s",
            source.id, len(chunks), len(to_embed),
        )
        return IngestionResult(
            source_id=source.id,
            chunks_total=len(chunks),
            chunks_embedded=len(to_embed),
            chunks_reused=len(chunks) - len(to_embed),
            embedding_model=model,
            input_tokens=input_tokens,
        )
