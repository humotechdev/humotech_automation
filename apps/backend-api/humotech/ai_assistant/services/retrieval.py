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

from django.db.models import F, Q, Value
from pgvector.django import CosineDistance

from humotech.ai_assistant.config import AiSettings, ai_settings
from humotech.ai_assistant.services.scoping import EmployeeScope
from humotech.core.enums import (
    SCOPE_LEVEL_GLOBAL,
    SCOPE_LEVEL_OFFICE,
    SCOPE_LEVEL_REGION,
)
from humotech.core.functions import SimpleTsQuery, SimpleTsVector, TsRank
from humotech.knowledge.models import FaqEntry, KnowledgeChunk

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


def _scope_condition(scope: EmployeeScope, prefix: str) -> Q:
    """Правило видно, если оно глобальное, либо ровно про его офис или регион.

    `prefix` — путь до полей области: у чанка это связь на источник,
    у FAQ — сама запись.
    """
    condition = Q(**{f"{prefix}office_id__isnull": True,
                     f"{prefix}region_id__isnull": True})
    if scope.office_id:
        condition |= Q(**{f"{prefix}office_id": scope.office_id})
    if scope.region_id:
        condition |= Q(**{f"{prefix}region_id": scope.region_id})
    return condition


class RetrievalService:
    def __init__(self, settings: AiSettings | None = None) -> None:
        self.settings = settings or ai_settings

    # --------------------------------------------------------------- фильтры

    def _visible_chunks(self, scope: EmployeeScope, language: str, today: date):
        """Чанки, которые вообще имеют право попасть в ответ.

        Отдел проверяется отдельно от уровня «офис > регион > глобальное»:
        документ отдела без офиса и региона — это не глобальное правило,
        а правило одного отдела. Раньше поле `department_id` принималось
        API, но поиск его не смотрел, и такой документ уходил всей
        организации.
        """
        department = Q(source__department_id__isnull=True)
        if scope.department_id:
            department |= Q(source__department_id=scope.department_id)
        return KnowledgeChunk.objects.filter(
            Q(source__effective_from__isnull=True)
            | Q(source__effective_from__lte=today),
            Q(source__effective_to__isnull=True)
            | Q(source__effective_to__gte=today),
            _scope_condition(scope, "source__"),
            department,
            source__organization_id=scope.organization_id,
            source__status="ACTIVE",
            source__language=language,
        )

    # ------------------------------------------------------------- точный FAQ

    def find_exact_faq(
        self, *, scope: EmployeeScope, question_vector: list[float], language: str
    ) -> FaqHit | None:
        """Ближайший утверждённый FAQ. Выше порога — ответ отдаётся без LLM."""
        row = (
            FaqEntry.objects.filter(
                _scope_condition(scope, ""),
                organization_id=scope.organization_id,
                status="ACTIVE",
                language=language,
                question_embedding__isnull=False,
            )
            .annotate(distance=CosineDistance("question_embedding", question_vector))
            .order_by("distance", "-priority")
            .values("id", "source_id", "canonical_question", "approved_answer",
                    "distance")
            .first()
        )
        if row is None:
            return None

        score = 1.0 - float(row["distance"])
        if score < self.settings.ai_exact_faq_threshold:
            return None
        return FaqHit(
            faq_id=row["id"],
            source_id=row["source_id"],
            question=row["canonical_question"],
            answer=row["approved_answer"],
            score=score,
        )

    # ---------------------------------------------------------- гибридный поиск

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
        base = self._visible_chunks(scope, language, today)
        limit = self.settings.ai_max_retrieved_chunks

        columns = (
            "id", "source_id", "chunk_index", "chunk_text",
            "source__title", "source__version", "source__updated_at",
            "source__published_at", "source__office_id", "source__region_id",
            "source__priority",
        )

        merged: dict[uuid.UUID, dict] = {}

        # --- половина первая: векторная близость ---
        vector_rows = (
            base.filter(embedding__isnull=False)
            .annotate(distance=CosineDistance("embedding", question_vector))
            .order_by("distance")
            .values(*columns, "distance")[: limit * 3]
        )
        for row in vector_rows:
            merged[row["id"]] = {
                "row": row,
                "vector": max(0.0, 1.0 - float(row["distance"])),
                "fts": 0.0,
            }

        # --- половина вторая: полнотекстовый поиск ---
        #
        # Выражение вектора задано вручную, а не через SearchVector: встроенный
        # оборачивает колонку в COALESCE, и планировщик перестаёт узнавать
        # GIN-индекс, построенный по чистому to_tsvector.
        tsvector = SimpleTsVector(F("chunk_text"))
        tsquery = SimpleTsQuery(Value(question))
        fts_rows = (
            base.annotate(rank=TsRank(tsvector, tsquery))
            .filter(rank__gt=0)
            .order_by("-rank")
            .values(*columns, "rank")[: limit * 3]
        )
        fts_rows = list(fts_rows)

        max_rank = max((float(r["rank"]) for r in fts_rows), default=0.0)
        for row in fts_rows:
            normalized = float(row["rank"]) / max_rank if max_rank > 0 else 0.0
            entry = merged.get(row["id"])
            if entry is None:
                merged[row["id"]] = {"row": row, "vector": 0.0, "fts": normalized}
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
                    source_id=row["source_id"],
                    source_title=row["source__title"],
                    source_version=row["source__version"],
                    source_updated_at=row["source__updated_at"],
                    published_at=row["source__published_at"],
                    chunk_index=row["chunk_index"],
                    text=row["chunk_text"],
                    score=score,
                    scope_level=_scope_level_of(
                        row["source__office_id"], row["source__region_id"]
                    ),
                    priority=row["source__priority"],
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
