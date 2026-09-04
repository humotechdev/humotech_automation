"""Очередь фоновых выгрузок.

Зачем отдельная таблица, а не общая с `knowledge_index_jobs`: у той есть
`source_id NOT NULL` на `knowledge_sources`, и выгрузка сотрудников не
может сослаться на статью базы знаний. Общей таблицы заданий в проекте
нет, поэтому заводится своя — но по той же схеме, что уже принята:
статус, попытки, время следующей попытки, захват строки исполнителем.

Очередь на самой PostgreSQL, без Redis и Celery. Исполнитель забирает
задание через `SELECT ... FOR UPDATE SKIP LOCKED`: два воркера никогда не
возьмут одну строку, а упавший воркер не держит её вечно — строка
возвращается в очередь по `next_attempt_at`.

Файл не лежит в базе. В базе только метаданные и ключ в хранилище;
сам файл отдаёт view, который сначала спрашивает, кому можно. Каталог
тот же приватный, что и у справок, — вне `MEDIA_URL`, веб-сервер из него
не раздаёт ничего.
"""

from __future__ import annotations

from django.db import models

from humotech.core.enums import EXPORT_JOB_STATUSES, choices, status_check
from humotech.core.models import (
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class ExportJob(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Одно задание на выгрузку.

    `filters` хранит параметры запроса целиком, и это не роскошь: без них
    на вопрос «что именно в этом файле» ответа нет, а файл живёт дольше
    экрана, с которого его заказали.

    `requested_by_user` — не просто след. Скачать файл может только тот,
    кто его заказал, и только пока действует его область видимости:
    выгрузка, сделанная по всей организации, не должна пережить перевод
    автора в один офис.
    """

    kind = models.CharField(max_length=50)
    fmt = models.CharField(max_length=10)
    status = models.CharField(
        max_length=20, choices=choices(EXPORT_JOB_STATUSES), db_default="QUEUED"
    )

    filters = models.JSONField(null=True, blank=True)
    requested_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.RESTRICT,
        related_name="export_jobs",
    )

    attempts = models.IntegerField(db_default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    # Захват строки исполнителем. Освобождается по времени, а не по
    # доверию к воркеру: процесс, убитый на середине, ничего не сообщает.
    locked_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    #: Сколько строк уже записано. Нужен, чтобы длинная выгрузка не
    #: выглядела зависшей: «идёт» без числа неотличимо от «умерла».
    progress_rows = models.IntegerField(db_default=0)
    total_rows = models.IntegerField(null=True, blank=True)

    storage_key = models.TextField(null=True, blank=True)
    file_name = models.CharField(max_length=255, null=True, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    #: Файл удаляется по сроку. Выгрузка кадровых данных, лежащая вечно, —
    #: это утечка, отложенная во времени.
    expires_at = models.DateTimeField(null=True, blank=True)

    #: Текст для человека, а не traceback. Внутренности наружу не идут:
    #: в сообщении об ошибке выгрузки нет ни путей, ни SQL, ни секретов.
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "export_jobs"
        constraints = [
            status_check("status", EXPORT_JOB_STATUSES, "ck_export_jobs_status"),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_export_jobs_organization_id"
            ),
            # Очередь читается ровно так: «взять готовое к запуску».
            models.Index(
                fields=["status", "next_attempt_at"],
                name="ix_export_jobs_status_next",
            ),
            models.Index(
                fields=["requested_by_user", "-created_at"],
                name="ix_export_jobs_user_time",
            ),
            # Уборка просроченных файлов ходит по этому индексу.
            models.Index(fields=["expires_at"], name="ix_export_jobs_expires_at"),
        ]

    def __str__(self) -> str:  # pragma: no cover - для админки
        return f"{self.kind}.{self.fmt} [{self.status}]"


__all__ = ["ExportJob"]
