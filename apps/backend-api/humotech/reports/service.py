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
from django.db.models import Count, Q
from django.utils import timezone

from humotech.core.enums import EXPORT_JOB_STATUSES
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.reports import storage
from humotech.reports.builder import ReportBuilderService, ReportSpec
from humotech.reports.catalog import REPORT_KINDS
from humotech.reports.sheets import EXPORT_KINDS
from humotech.reports.views import FORMATS

EXPORT_FIELDS = ("kind", "fmt", "status", "attempts", "file_name")

#: Все виды очереди: старые построители и виды конструктора.
JOB_KINDS = tuple(dict.fromkeys(EXPORT_KINDS + REPORT_KINDS))

#: Состояния списка. EXPIRED — не колонка, а готовое задание, чей срок
#: хранения прошёл: вкладка «Готовы» не должна обещать файл, которого нет.
LIST_STATUSES = EXPORT_JOB_STATUSES + ("EXPIRED",)

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
        return paginate(
            self._visible(actor, status=status, kind=kind, mine_only=mine_only),
            limit=limit,
            cursor=cursor,
        )

    def counts(self, actor: Actor, *, kind: str | None = None,
               mine_only: bool = True) -> dict[str, int]:
        """Сколько заданий в каждом состоянии — по ВСЕМУ доступному набору.

        Считает база, а не страница: длина загруженного списка — это
        длина загруженного списка, и выдавать её за итог значит показать
        «3 готовых» там, где их тридцать.

        Фильтр вкладки сюда не передаётся намеренно. Вкладка «Готовы» не
        должна менять число рядом с вкладкой «С ошибкой» — иначе счётчики
        показывают не набор, а сами себя.
        """
        rows = self._visible(actor, status=None, kind=kind, mine_only=mine_only)
        totals = {name: 0 for name in LIST_STATUSES}
        for row in rows.values("status").annotate(number=Count("id")):
            totals[row["status"]] = row["number"]
        totals["total"] = sum(totals.values())
        expired = rows.filter(_expired_q()).count()
        totals["SUCCEEDED"] -= expired
        totals["EXPIRED"] = expired
        return totals

    def _visible(self, actor: Actor, *, status, kind, mine_only: bool):
        """Задания, доступные этому человеку под этими фильтрами."""
        self.access.require(actor, "reports.export")
        rows = ExportJobQuerySet(actor).base().filter(hidden_at__isnull=True)
        # Чужие заказы видит только тот, кому положено видеть журнал.
        # Без права список молча остаётся своим: отказывать в ответ на
        # флаг, которого человек не выбирал, — плохая замена умолчанию.
        if mine_only or not self.access.has(actor, "audit.read"):
            rows = rows.filter(requested_by_user_id=actor.user_id)
        if status:
            # Несколько состояний через запятую: вкладка «В работе» —
            # это QUEUED и RUNNING, и склеивать их на клиенте значило бы
            # смешивать две страницы в одну.
            wanted = [
                _known(part, "status", LIST_STATUSES)
                for part in status.split(",")
                if part
            ]
            if wanted:
                condition = Q(status__in=[
                    item for item in wanted if item not in ("SUCCEEDED", "EXPIRED")
                ])
                if "SUCCEEDED" in wanted:
                    condition |= Q(status="SUCCEEDED") & ~_expired_q()
                if "EXPIRED" in wanted:
                    condition |= _expired_q()
                rows = rows.filter(condition)
        if kind is not None:
            rows = rows.filter(kind=_known(kind, "kind", JOB_KINDS))
        return rows

    def get(self, actor: Actor, job_id: uuid.UUID):
        self.access.require(actor, "reports.export")
        return self._require(actor, job_id)

    def create(
        self, actor: Actor, *, kind: str, fmt: str, filters: dict | None = None,
        spec: ReportSpec | None = None, client_request_id: uuid.UUID | None = None,
    ):
        """Поставить выгрузку в очередь.

        Параметры проверяются здесь, а не в исполнителе: ошибка в них,
        замеченная через минуту, приходит человеку, который уже ушёл
        с экрана, — и приходит в виде задания со статусом FAILED.

        `spec` — заказ конструктора. Для него права на вид и офисы
        проверяются сразу: кнопка, гарантированно рождающая красную строку
        в истории, хуже понятного отказа. При сборке они проверятся ещё раз.

        `client_request_id` — ключ повтора. Второй запрос с тем же ключом
        (двойной щелчок, повтор после обрыва сети) возвращает уже
        поставленное задание, а не ставит второе.
        """
        from humotech.reports.models import ExportJob

        self.access.require(actor, "reports.export")
        kind = _known(kind, "kind", JOB_KINDS)
        fmt = _known(fmt, "fmt", FORMATS)

        if client_request_id is not None:
            existing = ExportJob.objects.filter(
                organization_id=actor.organization_id,
                requested_by_user_id=actor.user_id,
                client_request_id=client_request_id,
            ).first()
            if existing is not None:
                return self._require(actor, existing.id)

        if spec is not None:
            ReportBuilderService().check(actor, spec)
            filters = spec.to_filters()
        elif kind not in EXPORT_KINDS:
            raise ValidationFailed(
                "Для этого отчёта нужны параметры конструктора",
                details={"kind": ["Укажите период и поля отчёта"]},
            )

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
                filters=filters if spec is not None else _serialize(filters or {}),
                status="QUEUED",
                client_request_id=client_request_id,
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
            locked.progress_done = 0
            locked.progress_total = None
            locked.total_rows = None
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

    def hide(self, actor: Actor, job_id: uuid.UUID) -> None:
        """Убрать выгрузку из своей истории.

        Запись остаётся — по ней журнал отвечает, кто что выгружал. Файл
        удаляется сразу: скрытая выгрузка, которую всё ещё можно скачать,
        была бы тем самым забытым файлом. Собираемую прямо сейчас убрать
        нельзя — исполнитель допишет её после; стоящая в очереди сначала
        отменяется.
        """
        from humotech.reports.models import ExportJob

        self.access.require(actor, "reports.export")
        job = self._require(actor, job_id)
        self._require_owner(actor, job)

        with self.atomic():
            locked = ExportJob.objects.select_for_update().get(id=job.id)
            if locked.hidden_at is not None:
                return
            if locked.status == "RUNNING":
                raise Conflict(
                    "Отчёт ещё формируется — удалить его можно после окончания",
                    details={"status": locked.status},
                )
            before = snapshot(locked, EXPORT_FIELDS)
            now = timezone.now()
            if locked.status == "QUEUED":
                locked.status = "CANCELLED"
                locked.finished_at = now
                locked.next_attempt_at = None
            storage.delete(locked.storage_key)
            locked.storage_key = None
            locked.size_bytes = None
            locked.hidden_at = now
            locked.save()
            self.audit.record(
                actor,
                action="export.job.hide",
                entity_type="export_jobs",
                entity_id=locked.id,
                before=before,
                after={**snapshot(locked, EXPORT_FIELDS), "hidden": True},
            )

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


def _expired_q() -> Q:
    """Готовое задание, срок хранения файла которого уже прошёл."""
    return Q(status="SUCCEEDED", expires_at__lte=timezone.now())


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
    "JOB_KINDS",
    "LIST_STATUSES",
    "RETRYABLE",
    "ExportJobService",
    "purge_expired",
    "retention_deadline",
]
