"""Иерархия правил и выявление конфликта.

Правило офиса перекрывает региональное, региональное — глобальное.
На одном уровне побеждает более высокий приоритет; при равном приоритете
и РАЗНЫХ документах вопрос уходит HR, а не решается моделью.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.core.database.enums import (
    SCOPE_LEVEL_GLOBAL,
    SCOPE_LEVEL_OFFICE,
    SCOPE_LEVEL_REGION,
)
from src.modules.ai_assistant.services.retrieval import (
    RetrievalService,
    RetrievedChunk,
)

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def chunk(
    *,
    score: float,
    scope_level: int,
    priority: int = 0,
    source_id: uuid.UUID | None = None,
    title: str = "Документ",
    published_at: datetime | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        source_id=source_id or uuid.uuid4(),
        source_title=title,
        source_version=1,
        source_updated_at=NOW,
        published_at=published_at or NOW,
        chunk_index=0,
        text="текст",
        score=score,
        scope_level=scope_level,
        priority=priority,
    )


@pytest.fixture()
def service(settings) -> RetrievalService:
    return RetrievalService(session=None, settings=settings)


def test_office_rule_beats_region_and_global(service):
    office = chunk(score=0.80, scope_level=SCOPE_LEVEL_OFFICE, title="Офисное")
    region = chunk(score=0.95, scope_level=SCOPE_LEVEL_REGION, title="Региональное")
    glob = chunk(score=0.99, scope_level=SCOPE_LEVEL_GLOBAL, title="Глобальное")

    result = service._apply_precedence([glob, region, office], limit=6)

    # даже с меньшим баллом побеждает более узкий уровень
    assert {c.source_title for c in result.chunks} == {"Офисное"}
    assert not result.conflict


def test_region_rule_beats_global(service):
    region = chunk(score=0.80, scope_level=SCOPE_LEVEL_REGION, title="Региональное")
    glob = chunk(score=0.99, scope_level=SCOPE_LEVEL_GLOBAL, title="Глобальное")

    result = service._apply_precedence([glob, region], limit=6)
    assert {c.source_title for c in result.chunks} == {"Региональное"}


def test_two_different_rules_on_same_level_escalate(service):
    """Равный уровень, равный приоритет, разные документы — решает HR."""
    first = chunk(score=0.90, scope_level=SCOPE_LEVEL_GLOBAL, title="Правило А")
    second = chunk(score=0.89, scope_level=SCOPE_LEVEL_GLOBAL, title="Правило Б")

    result = service._apply_precedence([first, second], limit=6)

    assert result.conflict is True
    assert len(result.conflicting_source_ids) == 2


def test_explicit_priority_resolves_conflict(service):
    """Если HR задал приоритет — конфликта нет, побеждает старший."""
    low = chunk(score=0.90, scope_level=SCOPE_LEVEL_GLOBAL, priority=0, title="Старое")
    high = chunk(score=0.89, scope_level=SCOPE_LEVEL_GLOBAL, priority=10, title="Главное")

    result = service._apply_precedence([low, high], limit=6)

    assert result.conflict is False
    assert {c.source_title for c in result.chunks} == {"Главное"}


def test_same_document_many_chunks_is_not_a_conflict(service):
    """Несколько кусков одного документа — это норма, а не противоречие."""
    source_id = uuid.uuid4()
    chunks = [
        chunk(score=0.90, scope_level=SCOPE_LEVEL_GLOBAL, source_id=source_id),
        chunk(score=0.88, scope_level=SCOPE_LEVEL_GLOBAL, source_id=source_id),
        chunk(score=0.87, scope_level=SCOPE_LEVEL_GLOBAL, source_id=source_id),
    ]
    result = service._apply_precedence(chunks, limit=6)
    assert result.conflict is False
    assert len(result.chunks) == 3


def test_distant_second_document_is_not_a_conflict(service):
    """Второй документ подходит заметно хуже — это не спор правил."""
    strong = chunk(score=0.95, scope_level=SCOPE_LEVEL_GLOBAL, title="Основное")
    weak = chunk(score=0.75, scope_level=SCOPE_LEVEL_GLOBAL, title="Побочное")

    result = service._apply_precedence([strong, weak], limit=6)
    assert result.conflict is False


def test_newer_publication_ranks_first_on_equal_score(service):
    """При равном балле и приоритете вперёд идёт более свежая публикация."""
    source_id = uuid.uuid4()
    older = chunk(
        score=0.90, scope_level=SCOPE_LEVEL_GLOBAL, source_id=source_id,
        published_at=NOW - timedelta(days=30),
    )
    newer = chunk(
        score=0.90, scope_level=SCOPE_LEVEL_GLOBAL, source_id=source_id,
        published_at=NOW,
    )
    result = service._apply_precedence([older, newer], limit=6)
    assert result.chunks[0].published_at == NOW


def test_limit_is_respected(service):
    chunks = [
        chunk(score=0.9 - i * 0.001, scope_level=SCOPE_LEVEL_GLOBAL,
              source_id=uuid.UUID(int=1))
        for i in range(20)
    ]
    result = service._apply_precedence(chunks, limit=6)
    assert len(result.chunks) == 6
