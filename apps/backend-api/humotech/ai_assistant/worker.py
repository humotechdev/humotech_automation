"""Фоновая индексация без брокера очередей.

Очередь — это сама таблица `knowledge_index_jobs`. Задача берётся запросом
    SELECT ... FOR UPDATE SKIP LOCKED
поэтому несколько воркеров работают параллельно и не мешают друг другу:
строку, которую уже взял сосед, PostgreSQL просто пропускает.

Redis и Celery для этого не нужны: объём задач — единицы в день, а надёжность
у транзакционной очереди в той же базе выше, чем у отдельного брокера,
потому что задача и данные коммитятся вместе.

Запуск:
    python manage.py index_knowledge            # один проход
    python manage.py index_knowledge --loop 10  # цикл с паузой
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from django.db import transaction
from django.db.models import Q

from humotech.ai_assistant.providers import build_embedding_provider
from humotech.ai_assistant.services.ingestion import KnowledgeIngestionService
from humotech.ai_assistant.services.publishing import KnowledgePublishingService
from humotech.knowledge.models import KnowledgeIndexJob

logger = logging.getLogger("humotech.ai.worker")

MAX_ATTEMPTS = 3
# растущая пауза между попытками: 1, 4, 9 минут
BACKOFF_BASE_SECONDS = 60


def claim_job() -> KnowledgeIndexJob | None:
    """Забирает одну задачу. Строку, взятую соседом, пропускает.

    Требует уже открытой транзакции: `select_for_update` без неё Django
    просто не выполнит — блокировка вне транзакции ничего не значила бы.
    """
    now = datetime.now(tz=timezone.utc)
    job = (
        KnowledgeIndexJob.objects.select_for_update(skip_locked=True)
        .filter(
            Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
            status="QUEUED",
        )
        .order_by("created_at")
        .first()
    )
    if job is None:
        return None

    job.status = "RUNNING"
    job.attempts += 1
    job.started_at = now
    job.save(update_fields=["status", "attempts", "started_at"])
    return job


def process_job(job: KnowledgeIndexJob, ingestion) -> bool:
    publishing = KnowledgePublishingService()
    try:
        result = ingestion.index_source(job.source_id)
    except Exception as exc:  # индексация не должна ронять воркер
        job.error_summary = str(exc)[:1000]
        job.finished_at = datetime.now(tz=timezone.utc)
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "FAILED"
            # Провал индексации НЕ выключает действующую версию:
            # сотрудники продолжают получать ответы из старой.
            publishing.mark_indexing_failed(job.source_id, str(exc))
        else:
            job.status = "QUEUED"
            job.next_attempt_at = datetime.now(tz=timezone.utc) + timedelta(
                seconds=BACKOFF_BASE_SECONDS * job.attempts**2
            )
        job.save()
        logger.exception("задача %s не выполнена (попытка %s)", job.id, job.attempts)
        return False

    job.status = "SUCCEEDED"
    job.finished_at = datetime.now(tz=timezone.utc)
    job.error_summary = None
    job.save()
    logger.info(
        "задача %s выполнена: кусков %s, новых эмбеддингов %s",
        job.id, result.chunks_total, result.chunks_embedded,
    )
    return True


def run_once(embeddings=None) -> bool:
    """Один проход. Возвращает True, если задача была взята.

    Захват задачи и её выполнение идут в ОДНОЙ транзакции: иначе воркер,
    упавший между захватом и работой, оставил бы строку в RUNNING навсегда.
    """
    embeddings = embeddings or build_embedding_provider()
    ingestion = KnowledgeIngestionService(embeddings)
    with transaction.atomic():
        job = claim_job()
        if job is None:
            return False
        process_job(job, ingestion)
        return True
