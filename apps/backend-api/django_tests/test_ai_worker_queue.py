"""Очередь индексации на живой базе: SKIP LOCKED, повторы, отказ.

Проверке блокировок нужны ДВЕ независимые транзакции: одна общая откатываемая
транзакция теста для этого не годится — второе соединение попросту не увидит
незакоммиченные строки. Поэтому такие тесты помечены
`django_db(transaction=True)`: они работают с настоящими коммитами
и убирают за собой сами.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from django.db import connections, transaction

from humotech.ai_assistant.providers.fake import FakeEmbeddingProvider
from humotech.ai_assistant.services.ingestion import KnowledgeIngestionService
from humotech.ai_assistant.services.publishing import KnowledgePublishingService
from humotech.ai_assistant.worker import MAX_ATTEMPTS, claim_job, process_job
from humotech.accounts.models import User
from humotech.employees.models import Employee
from humotech.knowledge.models import (
    KnowledgeChunk,
    KnowledgeIndexJob,
    KnowledgeSource,
)
from humotech.organizations.models import Organization

pytestmark = pytest.mark.django_db


def _world() -> tuple[Organization, KnowledgeSource]:
    """Организация, пользователь и источник со стоящей в очереди задачей."""
    org = Organization.objects.create(
        code=f"QUEUE{uuid.uuid4().hex[:6]}", name="Очередь",
        default_timezone="Asia/Dushanbe", status="ACTIVE",
    )
    employee = Employee.objects.create(
        organization=org, employee_number=f"EMP-{uuid.uuid4().hex[:6]}",
        first_name="Тест", last_name="Очередной",
        hire_date=date(2025, 1, 1), employment_status="ACTIVE",
    )
    user = User(
        organization=org, employee=employee,
        email=f"queue-{uuid.uuid4().hex[:6]}@humotech.tj", status="ACTIVE",
    )
    user.set_password("не важно")
    user.save()

    source = KnowledgeSource.objects.create(
        organization=org, title=f"Очередь {uuid.uuid4().hex[:6]}",
        source_type="POLICY", language="ru",
        content="Правило про очередь индексации.\n\nВторой абзац правила.",
        content_hash="0" * 64, status="DRAFT", version=1,
        created_by_user=user,
    )
    return org, source


def _queued_job(org, source, **overrides) -> KnowledgeIndexJob:
    payload = dict(
        organization=org, source=source, status="QUEUED", attempts=0,
        next_attempt_at=datetime.now(tz=timezone.utc) - timedelta(minutes=1),
    )
    payload.update(overrides)
    return KnowledgeIndexJob.objects.create(**payload)


def _ingestion() -> KnowledgeIngestionService:
    return KnowledgeIngestionService(FakeEmbeddingProvider(dimensions=1536))


# --- захват задачи ----------------------------------------------------------

def test_claim_takes_queued_job_and_marks_it_running():
    org, source = _world()
    job = _queued_job(org, source)

    with transaction.atomic():
        claimed = claim_job()
        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == "RUNNING"
        assert claimed.attempts == 1
        assert claimed.started_at is not None


def test_claim_ignores_jobs_scheduled_for_later():
    """Задача с отложенной повторной попыткой не берётся раньше срока."""
    org, source = _world()
    _queued_job(
        org, source,
        next_attempt_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
    )
    with transaction.atomic():
        assert claim_job() is None


def test_claim_ignores_jobs_in_other_statuses():
    org, source = _world()
    _queued_job(org, source, status="SUCCEEDED")
    with transaction.atomic():
        assert claim_job() is None


def test_empty_queue_returns_nothing():
    with transaction.atomic():
        assert claim_job() is None


# --- обработка --------------------------------------------------------------

def test_successful_indexing_marks_job_succeeded_and_creates_chunks():
    org, source = _world()
    job = _queued_job(org, source)

    with transaction.atomic():
        claimed = claim_job()
        assert process_job(claimed, _ingestion()) is True

    job.refresh_from_db()
    assert job.status == "SUCCEEDED"
    assert job.finished_at is not None
    assert job.error_summary is None
    assert KnowledgeChunk.objects.filter(source=source).count() > 0
    assert all(
        chunk.embedding is not None
        for chunk in KnowledgeChunk.objects.filter(source=source)
    )


def test_failure_reschedules_with_growing_backoff():
    """Первая неудача не хоронит задачу: она возвращается в очередь с паузой."""
    org, source = _world()
    job = _queued_job(org, source)

    class Failing:
        def index_source(self, source_id):
            raise RuntimeError("провайдер недоступен")

    with transaction.atomic():
        claimed = claim_job()
        assert process_job(claimed, Failing()) is False

    job.refresh_from_db()
    assert job.status == "QUEUED"
    assert job.attempts == 1
    assert job.next_attempt_at > datetime.now(tz=timezone.utc)
    assert "провайдер недоступен" in job.error_summary

    source.refresh_from_db()
    assert source.status != "ERROR", "одна неудача ещё не повод гасить источник"


def test_last_attempt_marks_job_failed_and_source_error():
    org, source = _world()
    job = _queued_job(org, source, attempts=MAX_ATTEMPTS - 1)

    class Failing:
        def index_source(self, source_id):
            raise RuntimeError("окончательно не вышло")

    with transaction.atomic():
        claimed = claim_job()
        assert process_job(claimed, Failing()) is False

    job.refresh_from_db()
    assert job.status == "FAILED"
    assert job.attempts == MAX_ATTEMPTS

    source.refresh_from_db()
    assert source.status == "ERROR"


def test_failed_indexing_does_not_disable_the_active_version():
    """Главное свойство двухфазной публикации: провал новой версии не оставляет
    компанию без базы знаний — старая продолжает отвечать."""
    org, source = _world()
    publishing = KnowledgePublishingService()

    # действующая версия
    active = KnowledgeSource.objects.create(
        organization=org, title=source.title, source_type="POLICY",
        language="ru", content="Действующее правило.", content_hash="1" * 64,
        status="ACTIVE", version=1, created_by_user=source.created_by_user,
        published_at=datetime.now(tz=timezone.utc),
    )

    job = _queued_job(org, source, attempts=MAX_ATTEMPTS - 1)

    class Failing:
        def index_source(self, source_id):
            raise RuntimeError("не вышло")

    with transaction.atomic():
        process_job(claim_job(), Failing())

    active.refresh_from_db()
    assert active.status == "ACTIVE", "действующая версия выключена — так нельзя"
    source.refresh_from_db()
    assert source.status == "ERROR"
    assert publishing.version_history(org.id, source.title, "ru")


# --- блокировки между воркерами ---------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_two_workers_do_not_take_the_same_job():
    """SKIP LOCKED: строку, взятую соседом, второй воркер пропускает.

    Ради этого теста и существует очередь без брокера. Проверять его в общей
    откатываемой транзакции бессмысленно: второе соединение не увидело бы
    незакоммиченную строку, и тест был бы зелёным при любой реализации.
    """
    org, source = _world()
    job = _queued_job(org, source)
    try:
        first_connection = connections["default"]
        second = connections.create_connection("default")
        try:
            with transaction.atomic():
                claimed = claim_job()
                assert claimed is not None and claimed.id == job.id

                # второй воркер работает своим соединением, пока первое
                # держит блокировку
                with second.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id FROM knowledge_index_jobs
                         WHERE status = 'QUEUED'
                           FOR UPDATE SKIP LOCKED
                        """
                    )
                    assert cursor.fetchone() is None, (
                        "второй воркер забрал ту же задачу — SKIP LOCKED не работает"
                    )
        finally:
            second.close()
    finally:
        KnowledgeIndexJob.objects.filter(organization=org).delete()
        KnowledgeChunk.objects.filter(source__organization=org).delete()
        KnowledgeSource.objects.filter(organization=org).delete()
        User.objects.filter(organization=org).delete()
        Employee.objects.filter(organization=org).delete()
        Organization.objects.filter(id=org.id).delete()


def test_reindexing_reuses_embeddings_of_unchanged_chunks():
    """Повторная индексация того же текста не платит провайдеру дважды."""
    org, source = _world()

    first = _ingestion().index_source(source.id)
    assert first.chunks_embedded == first.chunks_total
    assert first.chunks_reused == 0

    second = _ingestion().index_source(source.id)
    assert second.chunks_reused == second.chunks_total, (
        "текст не менялся — эмбеддинги должны быть переиспользованы"
    )
    assert second.chunks_embedded == 0
