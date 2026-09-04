"""Заказы на выгрузку: постановка, состояние, отмена, повтор, скачивание.

Очередь нужна там, где ждать всё равно придётся: выгрузка сессий за год
собирается минуты, и держать ради неё HTTP-соединение — значит получить
таймаут на балансировщике вместо файла. Мгновенные выгрузки остаются
как были (`reports/views.py`), очередь их не заменяет.

Три правила, из которых сделан этот модуль.

**Скачать файл может только заказчик.** Не «тот, у кого есть право на
выгрузку», а именно тот, кто её заказал. Файл собран по ЕГО области
видимости в момент сборки, и отдать его коллеге с другой областью
значило бы отдать данные, которых тот не видит на экране.

**Права проверяются при сборке, а не при заказе.** Заказ вчерашний,
сборка сегодняшняя, и роли за ночь могли измениться. Кадровик, которого
перевели в один офис, не должен получить файл по всей компании потому,
что нажал кнопку до перевода.

**Файл живёт по сроку.** Выгрузка кадровых данных, лежащая вечно, — это
утечка, отложенная во времени. Срок ставится при готовности файла и
считается от неё, а не от заказа: задание могло простоять в очереди.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from humotech.core.enums import EXPORT_JOB_STATUSES
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.reports import storage
from humotech.reports.sheets import EXPORT_KINDS
from humotech.reports.views import FORMATS

EXPORT_FIELDS = ("kind", "fmt", "status", "attempts", "file_name")

#: Состояния, из которых задание можно отменить.
#:
#: RUNNING отсутствует: строку держит исполнитель, и «отменено» рядом
#: с собираемым прямо сейчас файлом было бы неправдой. Зависшее задание
#: вернёт в очередь `reclaim_stale`.
CANCELLABLE = frozenset({"QUEUED"})

#: Состояния, из которых задание можно повторить.
RETRYABLE = frozenset({"FAILED", "CANCELLED"})


class ExportJobService(BaseService):
    """Фоновые выгрузки. Требует `reports.export`."""

    def list(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        kind: str | None = None,
        mine_only: bool = True,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """Свои заказы. Чужие — только с правом на журнал.

        Умолчание «только свои» не косметика: в списке видны фильтры
        выгрузки, а по ним читается, кто чем интересовался.
        """
        self.access.require(actor, "reports.export")
        queryset = ExportJobQuerySet(actor)
        rows = queryset.base()
        if mine_only or not self.access.has(actor, "audit.read"):
            rows = rows.filter(requested_by_user_id=actor.user_id)
        if status is not None:
            rows = rows.filter(status=_known(status, "status", EXPORT_JOB_STATUSES))
        if kind is not None:
            rows = rows.filter(kind=_known(kind, "kind", EXPORT_KINDS))
        return paginate(rows, limit=limit, cursor=cursor)

    def get(self, actor: Actor, job_id: uuid.UUID):
        self.access.require(actor, "reports.export")
        return self._require(actor, job_id)

    def create(
        self, actor: Actor, *, kind: str, fmt: str, filters: dict | None = None
    ):
        """Поставить выгрузку в очередь.

        Параметры проверяются здесь, а не в исполнителе: ошибка в них,
        замеченная через минуту, приходит человеку, который уже ушёл
        с экрана, — и приходит в виде задания со статусом FAILED.
        """
        from humotech.reports.models import ExportJob

        self.access.require(actor, "reports.export")
        kind = _known(kind, "kind", EXPORT_KINDS)
        fmt = _known(fmt, "fmt", FORMATS)

        pending = ExportJob.objects.filter(
            organization_id=actor.organization_id,
            requested_by_user_id=actor.user_id,
            status__in=("QUEUED", "RUNNING"),
        ).count()
        ceiling = settings.EXPORTS["MAX_PENDING_PER_USER"]
        if pending >= ceiling:
            raise Conflict(
                f"У вас уже {pending} незавершённых выгрузок. "
                "Дождитесь их окончания или отмените лишние",
                details={"pending": pending, "limit": ceiling},
            )

        with self.atomic():
            job = ExportJob.objects.create(
                organization_id=actor.organization_id,
                requested_by_user_id=actor.user_id,
                kind=kind,
                fmt=fmt,
                filters=_serialize(filters or {}),
                status="QUEUED",
            )
            self.audit.record(
                actor,
                action="export.job.create",
                entity_type="export_jobs",
                entity_id=job.id,
                after=snapshot(job, EXPORT_FIELDS),
            )
        return job

    def cancel(self, actor: Actor, job_id: uuid.UUID):
        from humotech.reports.models import ExportJob

        self.access.require(actor, "reports.export")
        job = self._require(actor, job_id)
        self._require_owner(actor, job)

        with self.atomic():
            locked = ExportJob.objects.select_for_update().get(id=job.id)
            if locked.status not in CANCELLABLE:
                raise Conflict(
                    "Эту выгрузку уже нельзя отменить",
                    details={"status": locked.status,
                             "cancellable_from": sorted(CANCELLABLE)},
                )
            before = snapshot(locked, EXPORT_FIELDS)
            locked.status = "CANCELLED"
            locked.next_attempt_at = None
            locked.finished_at = timezone.now()
            locked.save(
                update_fields=[
                    "status", "next_attempt_at", "finished_at", "updated_at",
                ]
            )
            self.audit.record(
                actor,
                action="export.job.cancel",
                entity_type="export_jobs",
                entity_id=locked.id,
                before=before,
                after=snapshot(locked, EXPORT_FIELDS),
            )
        return self._require(actor, job_id)

    def retry(self, actor: Actor, job_id: uuid.UUID):
        """Собрать выгрузку заново.

        Счётчик попыток обнуляется: автоматический предел защищает от
        молчаливого повторения вечно, а ручной повтор нажимают, уже
        разобравшись с причиной.
        """
        from humotech.reports.models import ExportJob

        self.access.require(actor, "reports.export")
        job = self._require(actor, job_id)
        self._require_owner(actor, job)

        with self.atomic():
            locked = ExportJob.objects.select_for_update().get(id=job.id)
            if locked.status not in RETRYABLE:
                raise Conflict(
                    "Эту выгрузку нельзя запустить повторно",
                    details={"status": locked.status,
                             "retryable_from": sorted(RETRYABLE)},
                )
            before = snapshot(locked, EXPORT_FIELDS)
            storage.delete(locked.storage_key)
            locked.status = "QUEUED"
            locked.attempts = 0
            locked.next_attempt_at = timezone.now()
            locked.locked_at = None
            locked.started_at = None
            locked.finished_at = None
            locked.error_message = None
            locked.progress_rows = 0
            locked.storage_key = None
            locked.file_name = None
            locked.size_bytes = None
            locked.expires_at = None
            locked.save()
            self.audit.record(
                actor,
                action="export.job.retry",
                entity_type="export_jobs",
                entity_id=locked.id,
                before=before,
                after=snapshot(locked, EXPORT_FIELDS),
            )
        return self._require(actor, job_id)

    def open_file(self, actor: Actor, job_id: uuid.UUID):
        """Файл готовой выгрузки: поток, имя и размер.

        Три условия, и каждое закрывает свою дыру: не заказчик — чужая
        область видимости; не SUCCEEDED — файла нет; просрочено — файл
        уже удалён или вот-вот будет.
        """
        self.access.require(actor, "reports.export")
        job = self._require(actor, job_id)
        self._require_owner(actor, job)

        if job.status != "SUCCEEDED" or not job.storage_key:
            raise Conflict(
                "Файл ещё не готов",
                details={"status": job.status},
            )
        if job.expires_at is not None and job.expires_at <= timezone.now():
            raise NotFound("Срок хранения файла истёк, закажите выгрузку заново")
        if not storage.exists(job.storage_key):
            raise NotFound("Файл выгрузки удалён, закажите её заново")

        self.audit.record(
            actor,
            action="export.job.download",
            entity_type="export_jobs",
            entity_id=job.id,
            after={"kind": job.kind, "fmt": job.fmt},
        )
        return storage.open_export(job.storage_key), job

    # ------------------------------------------------------------------ внутри

    def _require(self, actor: Actor, job_id: uuid.UUID):
        from humotech.reports.models import ExportJob

        job = (
            ExportJob.objects.select_related("requested_by_user")
            .filter(id=job_id, organization_id=actor.organization_id)
            .first()
        )
        if job is None:
            # Чужая организация отвечает как отсутствие записи.
            raise NotFound("Выгрузка не найдена")
        return job

    def _require_owner(self, actor: Actor, job) -> None:
        if job.requested_by_user_id != actor.user_id:
            raise NotFound("Выгрузка не найдена")


class ExportJobQuerySet:
    """Выборка заданий одной организации."""

    def __init__(self, actor: Actor) -> None:
        self.actor = actor

    def base(self):
        from humotech.reports.models import ExportJob

        return ExportJob.objects.filter(
            organization_id=self.actor.organization_id
        ).select_related("requested_by_user")


def retention_deadline(now=None):
    """До какого момента хранится файл, готовый прямо сейчас."""
    return (now or timezone.now()) + timedelta(
        hours=settings.EXPORTS["RETENTION_HOURS"]
    )


def purge_expired(now=None) -> int:
    """Удалить просроченные файлы. Возвращает число убранных.

    Запись задания остаётся: по ней видно, что выгрузка была и кем
    заказана. Уходит только файл — именно он и есть утечка.
    """
    from humotech.reports.models import ExportJob

    moment = now or timezone.now()
    stale = ExportJob.objects.filter(
        Q(storage_key__isnull=False) & ~Q(storage_key=""),
        expires_at__lte=moment,
    )
    removed = 0
    for job in stale:
        storage.delete(job.storage_key)
        job.storage_key = None
        job.size_bytes = None
        job.save(update_fields=["storage_key", "size_bytes", "updated_at"])
        removed += 1
    return removed


def _serialize(filters: dict) -> dict:
    """Фильтры в виде, который переживёт JSON.

    Даты и UUID сериализуются строками: обратно их разбирает
    исполнитель, и хранить их «как есть» значит получить объект,
    который не кладётся в JSONB.
    """
    result = {}
    for key, value in filters.items():
        if value is None:
            continue
        result[key] = value if isinstance(value, (int, float, bool)) else str(value)
    return result


def _known(value: str, field: str, allowed) -> str:
    if value not in allowed:
        raise ValidationFailed(
            f"Неизвестное значение параметра «{field}»",
            details={"field": field, "value": value, "allowed": list(allowed)},
        )
    return value


__all__ = [
    "CANCELLABLE",
    "RETRYABLE",
    "ExportJobService",
    "purge_expired",
    "retention_deadline",
]
