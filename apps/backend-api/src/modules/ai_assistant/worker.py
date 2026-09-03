"""Фоновая индексация без брокера очередей.

Очередь — это сама таблица `knowledge_index_jobs`. Задача берётся запросом
    SELECT ... FOR UPDATE SKIP LOCKED
поэтому несколько воркеров работают параллельно и не мешают друг другу:
строку, которую уже взял сосед, PostgreSQL просто пропускает.

Redis и Celery для этого не нужны: объём задач — единицы в день, а надёжность
у транзакционной очереди в той же базе выше, чем у отдельного брокера,
потому что задача и данные коммитятся вместе.

Запуск:
    python -m src.modules.ai_assistant.worker            # один проход
    python -m src.modules.ai_assistant.worker --loop 10  # цикл с паузой
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.session import session_scope
from src.modules.ai_assistant.config import ai_settings
from src.modules.ai_assistant.providers import build_embedding_provider
from src.modules.ai_assistant.services.ingestion import KnowledgeIngestionService
from src.modules.ai_assistant.services.publishing import KnowledgePublishingService
from src.modules.knowledge_base.models import KnowledgeIndexJob

logger = logging.getLogger("humotech.ai.worker")

MAX_ATTEMPTS = 3
# растущая пауза между попытками: 1, 4, 9 минут
BACKOFF_BASE_SECONDS = 60


def claim_job(session: Session) -> KnowledgeIndexJob | None:
    """Забирает одну задачу. Взятую соседом строка пропускается."""
    now = datetime.now(tz=timezone.utc)
    job = session.scalars(
        select(KnowledgeIndexJob)
        .where(
            KnowledgeIndexJob.status == "QUEUED",
            (KnowledgeIndexJob.next_attempt_at.is_(None))
            | (KnowledgeIndexJob.next_attempt_at <= now),
        )
        .order_by(KnowledgeIndexJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()

    if job is None:
        return None

    job.status = "RUNNING"
    job.attempts += 1
    job.started_at = now
    session.flush()
    return job


def process_job(session: Session, job: KnowledgeIndexJob, ingestion) -> bool:
    publishing = KnowledgePublishingService(session)
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
        session.flush()
        logger.exception("задача %s не выполнена (попытка %s)", job.id, job.attempts)
        return False

    job.status = "SUCCEEDED"
    job.finished_at = datetime.now(tz=timezone.utc)
    job.error_summary = None
    session.flush()
    logger.info(
        "задача %s выполнена: кусков %s, новых эмбеддингов %s",
        job.id, result.chunks_total, result.chunks_embedded,
    )
    return True


def run_once() -> bool:
    """Один проход. Возвращает True, если задача была взята."""
    embeddings = build_embedding_provider()
    ingestion_factory = lambda session: KnowledgeIngestionService(  # noqa: E731
        session, embeddings
    )
    with session_scope() as session:
        job = claim_job(session)
        if job is None:
            return False
        process_job(session, job, ingestion_factory(session))
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Воркер индексации базы знаний")
    parser.add_argument(
        "--loop", type=int, default=0,
        help="пауза в секундах между проходами; 0 — один проход и выход",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if not ai_settings.ai_assistant_enabled:
        logger.error(
            "AI_ASSISTANT_ENABLED=false — воркер не запускается. "
            "Это защита от случайного обращения к провайдеру."
        )
        raise SystemExit(2)

    if args.loop <= 0:
        run_once()
        return

    while True:
        try:
            if not run_once():
                time.sleep(args.loop)
        except KeyboardInterrupt:
            logger.info("воркер остановлен")
            return
        except Exception:
            logger.exception("непредвиденная ошибка воркера")
            time.sleep(args.loop)


if __name__ == "__main__":
    main()
