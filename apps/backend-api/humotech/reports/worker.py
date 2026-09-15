"""Исполнитель очереди выгрузок.

Тот же приём, что у индексации знаний и у отправки уведомлений:
`SELECT ... FOR UPDATE SKIP LOCKED` вместо брокера. Второй исполнитель
не встаёт в очередь за первым и не берёт ту же строку — он просто
берёт следующую.

Три решения, каждое против конкретной беды.

**Захват и работа — в разных транзакциях.** У индексации они в одной, и
там это верно: задача короткая. Здесь сборка отчёта идёт минуты, и
держать транзакцию всё это время значит держать блокировку строки и
снимок базы — на длинной выгрузке это раздувает журнал предзаписи.
Поэтому строка помечается RUNNING и коммитится сразу, а брошенную
работу возвращает по времени `reclaim_stale`: `locked_at` для того
и заведён.

**Отказ по правам не повторяется.** Права проверяются в момент сборки,
и «у автора больше нет доступа» повтором не лечится. Такое задание
сразу FAILED: повторять его — жечь попытки впустую и прятать причину.

**Наружу не уходит ни путь, ни SQL.** В `error_message` попадает текст
для человека; traceback остаётся в логе процесса.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from humotech.core.errors import DomainError, PermissionDenied, ValidationFailed
from humotech.core.rbac import Actor
from humotech.reports import heartbeat, storage
from humotech.reports.builder import (
    Progress, ReportBuilderService, ReportSpec, is_builder_order,
)
from humotech.reports.export import to_csv, to_xlsx
from humotech.reports.models import ExportJob
from humotech.reports.service import retention_deadline
from humotech.reports.sheets import build_sheet

logger = logging.getLogger("humotech.reports.worker")

#: Ошибки, которые повтор не исправит. Права и параметры за минуту
#: не меняются, и вторая попытка кончится ровно тем же.
PERMANENT = (PermissionDenied, ValidationFailed)


def claim_job(now: datetime | None = None) -> ExportJob | None:
    """Забрать одно задание. Взятое соседом — пропустить.

    Требует уже открытой транзакции: `select_for_update` вне транзакции
    Django не выполнит, да и блокировка там ничего не значила бы.
    """
    moment = now or timezone.now()
    job = (
        ExportJob.objects.select_for_update(skip_locked=True)
        .filter(
            Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=moment),
            status="QUEUED",
        )
        .order_by("created_at", "id")
        .first()
    )
    if job is None:
        return None

    job.status = "RUNNING"
    job.attempts += 1
    job.started_at = moment
    job.locked_at = moment
    job.progress_rows = 0
    job.save(
        update_fields=[
            "status", "attempts", "started_at", "locked_at",
            "progress_rows", "updated_at",
        ]
    )
    return job


def process_job(job: ExportJob) -> bool:
    """Собрать файл и записать его. Возвращает True при успехе."""
    if is_builder_order(job.filters):
        return _process_builder_job(job)
    try:
        sheet = build_sheet(
            job.kind,
            Actor(
                user_id=job.requested_by_user_id,
                organization_id=job.organization_id,
            ),
            filters=_restore(job.filters or {}),
            author=job.requested_by_user.email,
        )
        key = storage.storage_key_for(job.id, job.fmt)
        if job.fmt == "csv":
            size = storage.save_chunks(key, _counting(to_csv(sheet), job))
        else:
            size = storage.save_bytes(key, to_xlsx(sheet))
    except PERMANENT as exc:
        _fail(job, exc, permanent=True)
        return False
    except Exception as exc:  # noqa: BLE001 — воркер не должен падать
        _fail(job, exc, permanent=False)
        return False

    finished = timezone.now()
    job.status = "SUCCEEDED"
    job.storage_key = key
    job.file_name = _file_name(job)
    job.size_bytes = size
    job.finished_at = finished
    job.locked_at = None
    job.error_message = None
    job.next_attempt_at = None
    # Срок считается от готовности, а не от заказа: задание могло
    # простоять в очереди, и файл, родившийся уже просроченным, никому
    # не достался бы.
    job.expires_at = retention_deadline(finished)
    job.save()
    logger.info("выгрузка %s готова: %s байт", job.id, size)
    return True


def reclaim_stale(now: datetime | None = None) -> int:
    """Вернуть в очередь то, что зависло в RUNNING.

    Процесс мог упасть между захватом и результатом: блокировка строки
    снимется сама, статус — нет. Без этой уборки задание осталось бы
    RUNNING навсегда, и заказчик ждал бы файла, который никто не делает.
    """
    moment = now or timezone.now()
    deadline = moment - timedelta(
        seconds=settings.EXPORTS["LOCK_TIMEOUT_SECONDS"]
    )
    return ExportJob.objects.filter(
        status="RUNNING", locked_at__lt=deadline
    ).update(
        status="QUEUED", locked_at=None, next_attempt_at=moment,
        updated_at=moment,
    )


def run_once(now: datetime | None = None) -> bool:
    """Один проход очереди. True, если задание было взято.

    Захват идёт в своей транзакции и сразу коммитится: сборка отчёта
    длинная, и держать открытую транзакцию всё это время значит держать
    блокировку и снимок базы.
    """
    with transaction.atomic():
        job = claim_job(now)
    if job is None:
        return False
    logger.info(
        "взята выгрузка %s: %s.%s, попытка %s", job.id, job.kind, job.fmt, job.attempts,
    )
    process_job(job)
    return True


# ------------------------------------------------------------------- внутри


def _process_builder_job(job: ExportJob) -> bool:
    """Заказ конструктора: прогресс по шагам, листы XLSX, имя от человека.

    Знаменатель прогресса пишется до первой строки: страница показывает
    процент только тогда, когда знает, из скольких.
    """
    def total(steps: int) -> None:
        job.progress_total = steps
        ExportJob.objects.filter(id=job.id).update(progress_total=steps, progress_done=0)

    def sink(done: int, rows: int) -> None:
        heartbeat.beat()
        ExportJob.objects.filter(id=job.id).update(
            progress_done=done, progress_rows=rows, locked_at=timezone.now(),
        )

    progress = Progress(sink)
    try:
        spec = ReportSpec.from_filters(job.kind, job.filters or {})
        payload = ReportBuilderService().write(
            Actor(user_id=job.requested_by_user_id, organization_id=job.organization_id),
            spec,
            fmt=job.fmt,
            author=job.requested_by_user.email,
            progress=progress,
            total=total,
        )
        key = storage.storage_key_for(job.id, job.fmt)
        if job.fmt == "csv":
            size = storage.save_chunks(key, payload)
        else:
            size = storage.save_bytes(key, payload)
    except PERMANENT as exc:
        _fail(job, exc, permanent=True)
        return False
    except Exception as exc:  # noqa: BLE001 — воркер не должен падать
        _fail(job, exc, permanent=False)
        return False

    finished = timezone.now()
    job.status = "SUCCEEDED"
    job.storage_key = key
    job.file_name = spec.file_name(job.fmt)
    job.size_bytes = size
    job.progress_rows = progress.rows
    job.total_rows = progress.rows
    job.progress_done = job.progress_total or progress.done
    job.finished_at = finished
    job.locked_at = None
    job.error_message = None
    job.next_attempt_at = None
    job.expires_at = retention_deadline(finished)
    job.save()
    logger.info("выгрузка %s готова: %s байт, %s строк", job.id, size, progress.rows)
    return True


def _counting(chunks, job: ExportJob):
    """Считает записанные строки, чтобы длинная выгрузка не выглядела мёртвой.

    «Идёт» без числа неотличимо от «умерла». Счётчик пишется в базу
    редко: обновление на каждой строке стоило бы дороже самой выгрузки.
    """
    every = 500
    written = 0
    for chunk in chunks:
        written += 1
        if written % every == 0:
            heartbeat.beat()
            ExportJob.objects.filter(id=job.id).update(progress_rows=written)
        yield chunk
    ExportJob.objects.filter(id=job.id).update(progress_rows=written)
    # И в самом объекте тоже: `process_job` сохраняет его целиком, и без
    # этой строки итоговый `save()` затёр бы счётчик нулём, с которого
    # задание было захвачено.
    job.progress_rows = written


def _fail(job: ExportJob, exc: Exception, *, permanent: bool) -> None:
    moment = timezone.now()
    limit = settings.EXPORTS["MAX_ATTEMPTS"]
    job.locked_at = None
    job.error_message = _human_reason(exc)

    if permanent or job.attempts >= limit:
        job.status = "FAILED"
        job.next_attempt_at = None
        job.finished_at = moment
    else:
        job.status = "QUEUED"
        job.next_attempt_at = moment + timedelta(
            seconds=settings.EXPORTS["RETRY_BASE_SECONDS"] * job.attempts**2
        )
    job.save(
        update_fields=[
            "status", "locked_at", "error_message", "next_attempt_at",
            "finished_at", "updated_at",
        ]
    )
    # Подробности — в лог процесса, а не в поле, которое увидит кадровик.
    logger.warning(
        "выгрузка %s не собрана (попытка %s, окончательно=%s)",
        job.id, job.attempts, permanent or job.attempts >= limit,
        exc_info=True,
    )


def _human_reason(exc: Exception) -> str:
    """Текст для человека. Ни путей, ни SQL, ни внутренностей."""
    if isinstance(exc, DomainError):
        return exc.message[:500]
    if isinstance(exc, PermissionError):
        return "Не удалось записать файл выгрузки"
    return "Не удалось собрать выгрузку. Попробуйте позже"


def _file_name(job: ExportJob) -> str:
    """Имя, под которым файл придёт человеку.

    Отличается от имени на диске намеренно: на диске лежит
    идентификатор, а скачивается понятное имя с видом отчёта и датой.
    """
    stamp = date.today().isoformat()
    return f"humotech-{job.kind}-{stamp}.{job.fmt}"


def _restore(filters: dict) -> dict:
    """Фильтры из JSONB обратно в даты и UUID.

    В базе они лежат строками, а сервисы ждут разобранные значения.
    Неразбираемое отбрасывается: заказ проверен при постановке, и
    спорить с содержимым собственной колонки здесь незачем.
    """
    restored: dict = {}
    for key, value in filters.items():
        if value in (None, ""):
            continue
        if key in ("office_id", "region_id"):
            try:
                restored[key] = uuid.UUID(str(value))
            except (ValueError, TypeError):
                continue
        elif key in ("date", "date_from", "date_to"):
            try:
                restored[key] = date.fromisoformat(str(value))
            except (ValueError, TypeError):
                continue
        else:
            restored[key] = value
    return restored


__all__ = ["claim_job", "process_job", "reclaim_stale", "run_once"]
