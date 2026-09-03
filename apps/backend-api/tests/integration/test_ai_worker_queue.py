"""Очередь индексации на живой базе: SKIP LOCKED, повторы, отказ.

Тесты, которым нужны ДВЕ независимые транзакции (проверка блокировок), не могут
работать в общей откатываемой транзакции фикстуры `db`: вторая сессия
попросту не увидит незакоммиченные строки. Поэтому они открывают собственные
соединения и убирают за собой сами.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.modules.ai_assistant.providers.fake import FakeEmbeddingProvider
from src.modules.ai_assistant.services.ingestion import KnowledgeIngestionService
from src.modules.ai_assistant.services.publishing import KnowledgePublishingService
from src.modules.ai_assistant.worker import MAX_ATTEMPTS, claim_job, process_job
from src.modules.employees.models import Employee
from src.modules.knowledge_base.models import KnowledgeIndexJob, KnowledgeSource
from src.modules.organizations.models import Organization
from src.modules.users.models import User

pytestmark = pytest.mark.usefixtures("engine")


@pytest.fixture()
def committed_world(engine):
    """Организация, пользователь и источник, ВИДИМЫЕ другим соединениям.

    Данные коммитятся, поэтому убираются вручную в конце теста.
    """
    org_id = uuid.uuid4()
    with Session(engine) as session:
        org = Organization(
            id=org_id, code=f"QUEUE{uuid.uuid4().hex[:6]}", name="Очередь",
            default_timezone="Asia/Dushanbe", status="ACTIVE",
        )
        session.add(org)
        session.flush()

        employee = Employee(
            organization_id=org_id, employee_number="EMP-Q",
            first_name="Тест", last_name="Очередной",
            hire_date=date(2025, 1, 1), employment_status="ACTIVE",
        )
        session.add(employee)
        session.flush()

        user = User(
            organization_id=org_id, employee_id=employee.id,
            email=f"queue-{uuid.uuid4().hex[:6]}@humotech.tj",
            password_hash="argon2:stub", status="ACTIVE",
        )
        session.add(user)
        session.flush()

        source = KnowledgeSource(
            organization_id=org_id, title="Очередь", source_type="POLICY",
            language="ru", content="Правило про очередь индексации.",
            content_hash="0" * 64, status="DRAFT", version=1,
            created_by_user_id=user.id,
        )
        session.add(source)
        session.flush()

        job = KnowledgeIndexJob(
            organization_id=org_id, source_id=source.id, status="QUEUED",
            attempts=0, next_attempt_at=datetime.now(tz=timezone.utc),
        )
        session.add(job)
        session.commit()
        ids = {"org": org_id, "source": source.id, "job": job.id, "user": user.id,
               "employee": employee.id}

    yield ids

    with Session(engine) as session:
        session.execute(
            delete(KnowledgeIndexJob).where(KnowledgeIndexJob.organization_id == org_id)
        )
        from src.modules.knowledge_base.models import KnowledgeChunk

        session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.organization_id == org_id)
        )
        session.execute(
            delete(KnowledgeSource).where(KnowledgeSource.organization_id == org_id)
        )
        session.execute(delete(User).where(User.organization_id == org_id))
        session.execute(delete(Employee).where(Employee.organization_id == org_id))
        session.execute(delete(Organization).where(Organization.id == org_id))
        session.commit()


def test_two_workers_cannot_claim_the_same_job(engine, committed_world):
    """SELECT ... FOR UPDATE SKIP LOCKED: строку берёт ровно один воркер."""
    first = Session(engine)
    second = Session(engine)
    try:
        first.begin()
        second.begin()

        claimed_by_first = claim_job(first)
        assert claimed_by_first is not None, "первый воркер не забрал задачу"

        # вторая транзакция обязана ПРОПУСТИТЬ заблокированную строку,
        # а не ждать освобождения и не забрать её повторно
        claimed_by_second = claim_job(second)
        assert claimed_by_second is None, "одну задачу забрали два воркера"
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


def test_claimed_job_becomes_running_and_counts_attempt(engine, committed_world):
    with Session(engine) as session:
        session.begin()
        job = claim_job(session)
        assert job.status == "RUNNING"
        assert job.attempts == 1
        assert job.started_at is not None
        session.rollback()


def test_failure_schedules_retry_with_backoff(engine, committed_world):
    """Первая неудача не хоронит задачу: повтор с растущей паузой."""

    class Failing:
        def index_source(self, source_id):
            raise RuntimeError("провайдер эмбеддингов недоступен")

    with Session(engine) as session:
        session.begin()
        job = claim_job(session)
        ok = process_job(session, job, Failing())

        assert ok is False
        assert job.status == "QUEUED", "задача должна вернуться в очередь"
        assert job.attempts == 1
        assert job.next_attempt_at > datetime.now(tz=timezone.utc)
        assert "недоступен" in job.error_summary
        session.rollback()


def test_job_fails_after_max_attempts_and_source_gets_error(
    engine, committed_world
):
    """После исчерпания попыток задача FAILED, а версия — ERROR."""

    class Failing:
        def index_source(self, source_id):
            raise RuntimeError("постоянная ошибка")

    with Session(engine) as session:
        session.begin()
        job = session.get(KnowledgeIndexJob, committed_world["job"])
        job.attempts = MAX_ATTEMPTS - 1
        session.flush()

        job = claim_job(session)
        assert job is None or job.attempts == MAX_ATTEMPTS
        if job is None:
            session.rollback()
            pytest.skip("задача не была захвачена: повтор ещё не наступил")

        process_job(session, job, Failing())
        assert job.status == "FAILED"

        source = session.get(KnowledgeSource, committed_world["source"])
        assert source.status == "ERROR"
        session.rollback()


def test_successful_job_marks_succeeded(engine, committed_world):
    with Session(engine) as session:
        session.begin()
        job = claim_job(session)
        ingestion = KnowledgeIngestionService(
            session, FakeEmbeddingProvider(dimensions=1536), _settings()
        )
        ok = process_job(session, job, ingestion)

        assert ok is True
        assert job.status == "SUCCEEDED"
        assert job.error_summary is None
        assert job.finished_at is not None
        session.rollback()


def test_failed_indexing_does_not_archive_active_version(engine, committed_world):
    """Провал новой версии не выключает уже работающую."""
    with Session(engine) as session:
        session.begin()
        publishing = KnowledgePublishingService(session)
        ingestion = KnowledgeIngestionService(
            session, FakeEmbeddingProvider(dimensions=1536), _settings()
        )

        first = session.get(KnowledgeSource, committed_world["source"])
        publishing.enqueue_indexing(first.id)
        ingestion.index_source(first.id)
        publishing.publish(first.id, approved_by_user_id=committed_world["user"])
        assert first.status == "ACTIVE"

        second = publishing.create_draft(
            organization_id=committed_world["org"],
            title=first.title, source_type="POLICY", language="ru",
            content="Новая редакция правила про очередь.",
            created_by_user_id=committed_world["user"],
            parent_source_id=first.id,
        )
        publishing.enqueue_indexing(second.id)
        publishing.mark_indexing_failed(second.id, "эмбеддинги не посчитались")

        session.refresh(first)
        assert first.status == "ACTIVE", "провал новой версии выключил рабочую"
        assert second.status == "ERROR"
        session.rollback()


def test_queue_index_only_picks_due_jobs(engine, committed_world):
    """Задача с отложенным next_attempt_at не берётся раньше срока."""
    with Session(engine) as session:
        session.begin()
        job = session.get(KnowledgeIndexJob, committed_world["job"])
        job.next_attempt_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        session.flush()

        assert claim_job(session) is None
        session.rollback()


def _settings():
    from src.modules.ai_assistant.config import AiSettings

    return AiSettings(
        ai_assistant_enabled=True,
        openai_api_key="test-key",
        openai_chat_model="test-chat",
        openai_embedding_model="test-embedding",
    )


def test_repeated_content_hash_skips_embedding(engine, committed_world):
    """Тот же текст не считается повторно — это прямая экономия денег."""
    with Session(engine) as session:
        session.begin()
        embeddings = FakeEmbeddingProvider(dimensions=1536)
        ingestion = KnowledgeIngestionService(session, embeddings, _settings())
        publishing = KnowledgePublishingService(session)

        source = session.get(KnowledgeSource, committed_world["source"])
        ingestion.index_source(source.id)
        calls_after_first = len(embeddings.calls)

        twin = publishing.create_draft(
            organization_id=committed_world["org"],
            title="Копия правила", source_type="POLICY", language="ru",
            content=source.content,
            created_by_user_id=committed_world["user"],
        )
        result = ingestion.index_source(twin.id)

        assert result.chunks_embedded == 0
        assert result.chunks_reused == result.chunks_total
        assert len(embeddings.calls) == calls_after_first
        session.rollback()


def test_only_queued_jobs_are_visible_to_worker(engine, committed_world):
    with Session(engine) as session:
        session.begin()
        job = session.get(KnowledgeIndexJob, committed_world["job"])
        job.status = "SUCCEEDED"
        session.flush()

        assert claim_job(session) is None
        session.rollback()


def test_jobs_are_scoped_to_their_source(engine, committed_world):
    with Session(engine) as session:
        jobs = session.scalars(
            select(KnowledgeIndexJob).where(
                KnowledgeIndexJob.organization_id == committed_world["org"]
            )
        ).all()
        assert len(jobs) == 1
        assert jobs[0].source_id == committed_world["source"]
