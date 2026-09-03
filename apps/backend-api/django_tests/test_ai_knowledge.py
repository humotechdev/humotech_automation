"""Интеграционные проверки базы знаний: pgvector, версии, изоляция.

Этим тестам нужен настоящий PostgreSQL с расширением vector — подделать
векторный поиск, частичные индексы и атомарную смену версии нечем.
Поднимается контейнером:

    docker compose -f infrastructure/docker/docker-compose.yml up -d

Без TEST_DATABASE_URL тесты пропускаются, а не падают.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from django.db import connection

from humotech.ai_assistant.errors import PublishingError
from humotech.ai_assistant.providers.fake import FakeEmbeddingProvider
from humotech.ai_assistant.services.escalation import QuestionEscalationService
from humotech.ai_assistant.services.ingestion import KnowledgeIngestionService
from humotech.ai_assistant.services.publishing import (
    KnowledgePublishingService,
    current_revision,
)
from humotech.ai_assistant.services.retrieval import RetrievalService
from humotech.ai_assistant.services.scoping import (
    EmployeeScope,
    resolve_employee_scope,
)
from humotech.knowledge.models import KnowledgeChunk, KnowledgeSource
from humotech.accounts.models import User

pytestmark = pytest.mark.django_db

EMBEDDING_DIM = 1536


@pytest.fixture()
def hr_user(db, organization, employee) -> User:
    user = User(
        organization_id=organization.id,
        employee_id=employee.id,
        email=f"hr-{uuid.uuid4().hex[:6]}@humotech.tj",
        status="ACTIVE",
    )
    user.set_password("не важно")
    user.save()
    return user


@pytest.fixture()
def embeddings() -> FakeEmbeddingProvider:
    """Дублёр вместо OpenAI: интеграционные тесты тоже не ходят в сеть."""
    return FakeEmbeddingProvider(dimensions=EMBEDDING_DIM)


@pytest.fixture()
def publishing(db) -> KnowledgePublishingService:
    return KnowledgePublishingService()


@pytest.fixture()
def ingestion(db, embeddings, settings_for_ingestion) -> KnowledgeIngestionService:
    return KnowledgeIngestionService(embeddings, settings_for_ingestion)


@pytest.fixture()
def settings_for_ingestion():
    from humotech.ai_assistant.config import AiSettings

    return AiSettings(
        ai_assistant_enabled=True,
        openai_api_key="test-key",
        openai_chat_model="test-chat",
        openai_embedding_model="test-embedding",
        ai_rag_min_score=0.0,
        ai_exact_faq_threshold=0.92,
    )


def make_active_source(
    db, publishing, ingestion, hr_user, organization, *,
    title: str, content: str, office_id=None, region_id=None,
    language: str = "ru", effective_from=None, effective_to=None, priority: int = 0,
) -> KnowledgeSource:
    source = publishing.create_draft(
        organization_id=organization.id,
        title=title,
        source_type="POLICY",
        language=language,
        content=content,
        created_by_user_id=hr_user.id,
        office_id=office_id,
        region_id=region_id,
        effective_from=effective_from,
        effective_to=effective_to,
        priority=priority,
    )
    publishing.enqueue_indexing(source.id)
    ingestion.index_source(source.id)
    publishing.publish(source.id, approved_by_user_id=hr_user.id)
    return source


def scope_for(db, employee) -> EmployeeScope:
    scope = resolve_employee_scope(employee_id=employee.id)
    assert scope is not None
    return scope


# ------------------------------------------------------------- pgvector жив

def test_pgvector_extension_is_available(db):
    """Без расширения vector остальные тесты этого файла бессмысленны."""

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        installed = cursor.fetchone()
    assert installed is not None, (
        "расширение pgvector не установлено в тестовой базе. "
        "Поднимите контейнер: docker compose -f "
        "infrastructure/docker/docker-compose.yml up -d"
    )


def test_vector_column_accepts_and_searches(db, organization, hr_user,
                                            publishing, ingestion, embeddings):
    source = make_active_source(
        db, publishing, ingestion, hr_user, organization,
        title="Отпуска", content="Отпуск оформляется за четырнадцать дней.",
    )
    stored = list(KnowledgeChunk.objects.filter(source_id=source.id))
    assert stored, "куски не сохранились"
    assert stored[0].embedding is not None
    assert len(stored[0].embedding) == EMBEDDING_DIM


# ------------------------------------------------------ только ACTIVE и по сроку

def test_draft_and_archived_are_not_searchable(db, organization, employee, hr_user,
                                               publishing, ingestion, embeddings,
                                               settings_for_ingestion):
    draft = publishing.create_draft(
        organization_id=organization.id, title="Черновик",
        source_type="POLICY", language="ru",
        content="Секретное правило про такси.",
        created_by_user_id=hr_user.id,
    )
    publishing.enqueue_indexing(draft.id)
    ingestion.index_source(draft.id)
    # НЕ публикуем

    scope = scope_for(db, employee)
    vector = embeddings.embed(["такси"], model="m").vectors[0]
    result = RetrievalService(settings_for_ingestion).search(
        scope=scope, question="такси", question_vector=vector, language="ru"
    )
    assert result.is_empty, "непубликованный черновик попал в поиск"


def test_expired_knowledge_is_not_used(db, organization, employee, hr_user,
                                       publishing, ingestion, embeddings,
                                       settings_for_ingestion):
    yesterday = date.today() - timedelta(days=1)
    make_active_source(
        db, publishing, ingestion, hr_user, organization,
        title="Старое правило", content="Компенсация проезда двести сомони.",
        effective_from=yesterday - timedelta(days=30), effective_to=yesterday,
    )

    scope = scope_for(db, employee)
    vector = embeddings.embed(["компенсация проезда"], model="m").vectors[0]
    result = RetrievalService(settings_for_ingestion).search(
        scope=scope, question="компенсация проезда",
        question_vector=vector, language="ru",
    )
    assert result.is_empty, "просроченное правило попало в поиск"


def test_future_knowledge_is_not_used_yet(db, organization, employee, hr_user,
                                          publishing, ingestion, embeddings,
                                          settings_for_ingestion):
    tomorrow = date.today() + timedelta(days=1)
    make_active_source(
        db, publishing, ingestion, hr_user, organization,
        title="Будущее правило", content="С будущего месяца новый порядок.",
        effective_from=tomorrow,
    )
    scope = scope_for(db, employee)
    vector = embeddings.embed(["новый порядок"], model="m").vectors[0]
    result = RetrievalService(settings_for_ingestion).search(
        scope=scope, question="новый порядок",
        question_vector=vector, language="ru",
    )
    assert result.is_empty


# ------------------------------------------------------------ изоляция офисов

def test_other_office_rule_is_invisible(db, organization, employee, other_office,
                                        hr_user, publishing, ingestion, embeddings,
                                        settings_for_ingestion):
    make_active_source(
        db, publishing, ingestion, hr_user, organization,
        title="Правило филиала", content="В филиале обед с 13 до 14.",
        office_id=other_office.id,
    )
    scope = scope_for(db, employee)
    vector = embeddings.embed(["обед"], model="m").vectors[0]
    result = RetrievalService(settings_for_ingestion).search(
        scope=scope, question="обед", question_vector=vector, language="ru"
    )
    assert result.is_empty, "правило чужого офиса видно сотруднику"


def test_global_rule_is_visible_to_everyone(db, organization, employee, hr_user,
                                            publishing, ingestion, embeddings,
                                            settings_for_ingestion):
    make_active_source(
        db, publishing, ingestion, hr_user, organization,
        title="Общий регламент", content="Рабочий день начинается в девять утра.",
    )
    scope = scope_for(db, employee)
    vector = embeddings.embed(["рабочий день начинается"], model="m").vectors[0]
    result = RetrievalService(settings_for_ingestion).search(
        scope=scope, question="рабочий день начинается",
        question_vector=vector, language="ru",
    )
    assert not result.is_empty


def test_other_language_is_not_returned(db, organization, employee, hr_user,
                                        publishing, ingestion, embeddings,
                                        settings_for_ingestion):
    make_active_source(
        db, publishing, ingestion, hr_user, organization,
        title="English policy", content="Vacation is requested two weeks ahead.",
        language="en",
    )
    scope = scope_for(db, employee)
    vector = embeddings.embed(["vacation"], model="m").vectors[0]
    result = RetrievalService(settings_for_ingestion).search(
        scope=scope, question="vacation", question_vector=vector, language="ru"
    )
    assert result.is_empty


# ---------------------------------------------------------- версии и публикация

def test_new_version_activates_atomically(db, organization, employee, hr_user,
                                          publishing, ingestion, embeddings):
    first = make_active_source(
        db, publishing, ingestion, hr_user, organization,
        title="Отпуска", content="Отпуск за четырнадцать дней.",
    )
    revision_before = current_revision(organization.id)

    second = publishing.create_draft(
        organization_id=organization.id, title="Отпуска",
        source_type="POLICY", language="ru",
        content="Отпуск оформляется за двадцать один день.",
        created_by_user_id=hr_user.id, parent_source_id=first.id,
    )
    publishing.enqueue_indexing(second.id)
    ingestion.index_source(second.id)

    # до публикации действует старая версия
    first.refresh_from_db()
    assert first.status == "ACTIVE"

    result = publishing.publish(second.id, approved_by_user_id=hr_user.id)

    first.refresh_from_db()
    second.refresh_from_db()
    assert second.status == "ACTIVE"
    assert first.status == "ARCHIVED"
    assert result.archived_source_id == first.id
    assert result.knowledge_revision == revision_before + 1


def test_failed_indexing_keeps_old_version_active(db, organization, hr_user,
                                                  publishing, ingestion):
    first = make_active_source(
        db, publishing, ingestion, hr_user, organization,
        title="Больничные", content="Справка приносится в течение трёх дней.",
    )
    second = publishing.create_draft(
        organization_id=organization.id, title="Больничные",
        source_type="POLICY", language="ru",
        content="Новая редакция правил больничного.",
        created_by_user_id=hr_user.id, parent_source_id=first.id,
    )
    publishing.enqueue_indexing(second.id)
    publishing.mark_indexing_failed(second.id, "провайдер эмбеддингов недоступен")

    first.refresh_from_db()
    second.refresh_from_db()
    assert second.status == "ERROR"
    assert first.status == "ACTIVE", "провал индексации выключил рабочую версию"


def test_publishing_without_index_is_refused(db, organization, hr_user, publishing):
    draft = publishing.create_draft(
        organization_id=organization.id, title="Без индекса",
        source_type="POLICY", language="ru", content="Текст без эмбеддингов.",
        created_by_user_id=hr_user.id,
    )
    with pytest.raises(PublishingError, match="Публиковать нечего"):
        publishing.publish(draft.id, approved_by_user_id=hr_user.id)


def test_publish_bumps_revision_and_invalidates_cache_keys(
    db, organization, hr_user, publishing, ingestion
):
    from humotech.ai_assistant.services.cache import CacheKeyParts

    before = current_revision(organization.id)
    make_active_source(
        db, publishing, ingestion, hr_user, organization,
        title="Командировки", content="Суточные выплачиваются авансом.",
    )
    after = current_revision(organization.id)
    assert after == before + 1

    def key(revision: int) -> str:
        return CacheKeyParts(
            normalized_question_hash="a" * 64, language="ru",
            office_id="o", region_id="r", scope_fingerprint="f",
            knowledge_revision=revision, prompt_version="v1", model="m",
        ).build()

    assert key(before) != key(after)


def test_unchanged_chunks_are_not_re_embedded(db, organization, hr_user,
                                              publishing, ingestion, embeddings):
    """content_hash экономит деньги: тот же абзац не считается дважды."""
    content = "Правило про пропуска.\n\nПравило про парковку."
    first = publishing.create_draft(
        organization_id=organization.id, title="Пропуска",
        source_type="POLICY", language="ru", content=content,
        created_by_user_id=hr_user.id,
    )
    publishing.enqueue_indexing(first.id)
    ingestion.index_source(first.id)
    calls_after_first = len(embeddings.calls)

    second = publishing.create_draft(
        organization_id=organization.id, title="Пропуска-2",
        source_type="POLICY", language="ru", content=content,
        created_by_user_id=hr_user.id,
    )
    publishing.enqueue_indexing(second.id)
    result = ingestion.index_source(second.id)

    assert result.chunks_embedded == 0, "тот же текст посчитан повторно"
    assert result.chunks_reused == result.chunks_total
    assert len(embeddings.calls) == calls_after_first


# ------------------------------------------------------- неотвеченные вопросы

def test_unknown_question_is_saved_for_hr(db, employee):
    scope = scope_for(db, employee)
    service = QuestionEscalationService()

    record = service.escalate(
        scope=scope, question_text="Есть ли корпоративный транспорт?",
        normalized_hash="b" * 64, language="ru", best_score=0.3,
    )
    assert record.status == "NEW"
    assert record.occurrences_count == 1


def test_repeated_unknown_questions_are_aggregated(db, employee):
    scope = scope_for(db, employee)
    service = QuestionEscalationService()

    first = service.escalate(
        scope=scope, question_text="Есть ли корпоративный транспорт?",
        normalized_hash="c" * 64, language="ru", best_score=0.30,
    )
    second = service.escalate(
        scope=scope, question_text="а есть корпоративный транспорт",
        normalized_hash="c" * 64, language="ru", best_score=0.45,
    )

    assert second.id == first.id, "повтор создал новую строку вместо счётчика"
    assert second.occurrences_count == 2
    # лучший достигнутый балл сохраняется — HR видит, насколько близко было
    assert float(second.best_retrieval_score) == pytest.approx(0.45)
