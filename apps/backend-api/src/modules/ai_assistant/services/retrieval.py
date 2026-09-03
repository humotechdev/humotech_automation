"""Поиск подтверждённых знаний под конкретного сотрудника.

Гибрид: векторная близость (pgvector, cosine) + полнотекстовый поиск
PostgreSQL + фильтрация по метаданным. Векторный поиск хорошо ловит смысл,
но плохо — точные термины и номера; полнотекстовый наоборот. По отдельности
каждый регулярно промахивается.

Что вообще может попасть в выдачу:
    * статус ACTIVE;
    * язык совпадает;
    * дата вступила в силу (effective_from) и не истекла (effective_to);
    * область действия доступна сотруднику: глобальное правило,
      либо его регион, либо его офис.

Приоритет при совпадении: офис > регион > глобальное. На одном уровне
побеждает более высокий `priority`, при равном — более новая опубликованная
версия. Если на одном уровне сопоставимо подходят РАЗНЫЕ документы с равным
приоритетом — это конфликт: выбирать между ними должен HR, а не модель.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from src.core.database.enums import (
    SCOPE_LEVEL_GLOBAL,
    SCOPE_LEVEL_OFFICE,
    SCOPE_LEVEL_REGION,
)
from src.modules.ai_assistant.config import AiSettings, ai_settings
from src.modules.ai_assistant.services.scoping import EmployeeScope
from src.modules.knowledge_base.models import (
    FaqEntry,
    KnowledgeChunk,
    KnowledgeSource,
)

# Вклад каждой половины гибрида. Значения предварительные: окончательные
# выставим после прогона на реальных вопросах.
VECTOR_WEIGHT = 0.7
FTS_WEIGHT = 0.3


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    source_title: str
    source_version: int
    source_updated_at: datetime | None
    published_at: datetime | None
    chunk_index: int
    text: str
    score: float
    scope_level: int
    priority: int


@dataclass(frozen=True)
class FaqHit:
    faq_id: uuid.UUID
    source_id: uuid.UUID | None
    question: str
    answer: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    chunks: tuple[RetrievedChunk, ...] = ()
    top_score: float = 0.0
    conflict: bool = False
    conflicting_source_ids: tuple[uuid.UUID, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.chunks


class RetrievalService:
    def __init__(self, session: Session, settings: AiSettings | None = None) -> None:
        self.session = session
        self.settings = settings or ai_settings

    # --------------------------------------------------------------- фильтры

    def _visibility_filters(self, scope: EmployeeScope, language: str, today: date):
        """Условия, без которых запись вообще не имеет права попасть в ответ."""
        scope_filter = or_(
            and_(
                KnowledgeSource.office_id.is_(None),
                KnowledgeSource.region_id.is_(None),
            ),
            KnowledgeSource.office_id == scope.office_id
            if scope.office_id
            else False,
            KnowledgeSource.region_id == scope.region_id
            if scope.region_id
            else False,
        )
        return [
            KnowledgeSource.organization_id == scope.organization_id,
            KnowledgeSource.status == "ACTIVE",
            KnowledgeSource.language == language,
            or_(
                KnowledgeSource.effective_from.is_(None),
                KnowledgeSource.effective_from <= today,
            ),
            or_(
                KnowledgeSource.effective_to.is_(None),
                KnowledgeSource.effective_to >= today,
            ),
            scope_filter,
        ]

    # ------------------------------------------------------------- точный FAQ

    def find_exact_faq(
        self,
        *,
        scope: EmployeeScope,
        question_vector: list[float],
        language: str,
    ) -> FaqHit | None:
        """Ближайший утверждённый FAQ. Выше порога — ответ отдаётся без LLM."""
        faq_scope = or_(
            and_(FaqEntry.office_id.is_(None), FaqEntry.region_id.is_(None)),
            FaqEntry.office_id == scope.office_id if scope.office_id else False,
            FaqEntry.region_id == scope.region_id if scope.region_id else False,
        )
        distance = FaqEntry.question_embedding.cosine_distance(question_vector)
        row = self.session.execute(
            select(
                FaqEntry.id,
                FaqEntry.source_id,
                FaqEntry.canonical_question,
                FaqEntry.approved_answer,
                distance.label("distance"),
            )
            .where(
                FaqEntry.organization_id == scope.organization_id,
                FaqEntry.status == "ACTIVE",
                FaqEntry.language == language,
                FaqEntry.question_embedding.is_not(None),
                faq_scope,
            )
            .order_by(distance, FaqEntry.priority.desc())
            .limit(1)
        ).first()

        if row is None:
            return None

        score = 1.0 - float(row.distance)
        if score < self.settings.ai_exact_faq_threshold:
            return None
        return FaqHit(
            faq_id=row.id,
            source_id=row.source_id,
            question=row.canonical_question,
            answer=row.approved_answer,
            score=score,
        )

    # ------------------------------------------------------------ гибридный поиск

    def search(
        self,
        *,
        scope: EmployeeScope,
        question: str,
        question_vector: list[float],
        language: str,
        today: date | None = None,
    ) -> RetrievalResult:
        today = today or date.today()
        filters = self._visibility_filters(scope, language, today)
        limit = self.settings.ai_max_retrieved_chunks

        merged: dict[uuid.UUID, dict] = {}

        # --- половина первая: векторная близость ---
        distance = KnowledgeChunk.embedding.cosine_distance(question_vector)
        vector_rows = self.session.execute(
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.source_id,
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.chunk_text,
                KnowledgeSource.title,
                KnowledgeSource.version,
                KnowledgeSource.updated_at,
                KnowledgeSource.published_at,
                KnowledgeSource.office_id,
                KnowledgeSource.region_id,
                KnowledgeSource.priority,
                distance.label("distance"),
            )
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
            .where(KnowledgeChunk.embedding.is_not(None), *filters)
            .order_by(distance)
            .limit(limit * 3)
        ).all()

        for row in vector_rows:
            merged[row.id] = {
                "row": row,
                "vector": max(0.0, 1.0 - float(row.distance)),
                "fts": 0.0,
            }

        # --- половина вторая: полнотекстовый поиск ---
        tsquery = func.plainto_tsquery("simple", question)
        tsvector = func.to_tsvector("simple", KnowledgeChunk.chunk_text)
        rank = func.ts_rank(tsvector, tsquery)
        fts_rows = self.session.execute(
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.source_id,
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.chunk_text,
                KnowledgeSource.title,
                KnowledgeSource.version,
                KnowledgeSource.updated_at,
                KnowledgeSource.published_at,
                KnowledgeSource.office_id,
                KnowledgeSource.region_id,
                KnowledgeSource.priority,
                rank.label("rank"),
            )
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
            .where(tsvector.op("@@")(tsquery), *filters)
            .order_by(rank.desc())
            .limit(limit * 3)
        ).all()

        max_rank = max((float(r.rank) for r in fts_rows), default=0.0)
        for row in fts_rows:
            normalized = float(row.rank) / max_rank if max_rank > 0 else 0.0
            entry = merged.get(row.id)
            if entry is None:
                merged[row.id] = {"row": row, "vector": 0.0, "fts": normalized}
            else:
                entry["fts"] = normalized

        candidates: list[RetrievedChunk] = []
        for chunk_id, data in merged.items():
            row = data["row"]
            score = VECTOR_WEIGHT * data["vector"] + FTS_WEIGHT * data["fts"]
            if score < self.settings.ai_rag_min_score:
                continue
            candidates.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    source_id=row.source_id,
                    source_title=row.title,
                    source_version=row.version,
                    source_updated_at=row.updated_at,
                    published_at=row.published_at,
                    chunk_index=row.chunk_index,
                    text=row.chunk_text,
                    score=score,
                    scope_level=_scope_level_of(row.office_id, row.region_id),
                    priority=row.priority,
                )
            )

        if not candidates:
            return RetrievalResult()

        return self._apply_precedence(candidates, limit=limit)

    # ------------------------------------------------------------- приоритеты

    def _apply_precedence(
        self, candidates: list[RetrievedChunk], *, limit: int
    ) -> RetrievalResult:
        """Иерархия офис > регион > глобальное и выявление конфликта."""
        top_level = max(chunk.scope_level for chunk in candidates)
        leading = [c for c in candidates if c.scope_level == top_level]
        leading.sort(key=lambda c: c.score, reverse=True)

        top_score = leading[0].score
        # документы, сопоставимо подходящие под вопрос на верхнем уровне
        delta = self.settings.ai_conflict_score_delta
        near_top = [c for c in leading if c.score >= top_score - delta]

        by_source: dict[uuid.UUID, RetrievedChunk] = {}
        for chunk in near_top:
            best = by_source.get(chunk.source_id)
            if best is None or chunk.score > best.score:
                by_source[chunk.source_id] = chunk

        conflict = False
        conflicting: tuple[uuid.UUID, ...] = ()
        if len(by_source) > 1:
            priorities = {c.priority for c in by_source.values()}
            if len(priorities) > 1:
                # приоритет задан явно — берём победителя, конфликта нет
                winner = max(priorities)
                allowed = {
                    sid for sid, c in by_source.items() if c.priority == winner
                }
                leading = [c for c in leading if c.source_id in allowed]
            else:
                # разные документы одного уровня с равным приоритетом:
                # выбирать между ними должен HR
                conflict = True
                conflicting = tuple(sorted(by_source, key=str))

        # при равном приоритете внутри одного документа побеждает более
        # свежая публикация — сортируем по ней вторым ключом
        leading.sort(
            key=lambda c: (
                c.score,
                c.priority,
                c.published_at.timestamp() if c.published_at else 0.0,
            ),
            reverse=True,
        )
        return RetrievalResult(
            chunks=tuple(leading[:limit]),
            top_score=top_score,
            conflict=conflict,
            conflicting_source_ids=conflicting,
        )


def _scope_level_of(office_id, region_id) -> int:
    if office_id is not None:
        return SCOPE_LEVEL_OFFICE
    if region_id is not None:
        return SCOPE_LEVEL_REGION
    return SCOPE_LEVEL_GLOBAL
