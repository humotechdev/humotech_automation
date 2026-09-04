"""Уведомления сотрудникам: Telegram, почта, push, внутри интерфейса.

Таблица работает как transactional outbox. Строка создаётся в той же
транзакции, что и само изменение — заявка и уведомление о ней либо есть оба,
либо нет ни одного. Отправщик — отдельный процесс: он забирает строки
`SELECT ... FOR UPDATE SKIP LOCKED`, поэтому несколько отправщиков не берут
одну и ту же строку и не блокируют друг друга.

Очередь именно в PostgreSQL, без брокера: Celery в проекте нет, а гарантия
«не отправим то, чего не произошло» здесь важнее пропускной способности.
"""

from __future__ import annotations

from django.db import models

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    NOTIFICATION_CHANNELS,
    NOTIFICATION_STATUSES,
    choices,
    status_check,
)
from humotech.core.models import (
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class Notification(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="notifications",
    )
    channel = models.CharField(max_length=20, choices=choices(NOTIFICATION_CHANNELS))
    notification_type = models.CharField(max_length=100)
    title = models.CharField(max_length=255, null=True, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=choices(NOTIFICATION_STATUSES))
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    related_entity_type = models.CharField(max_length=100, null=True, blank=True)
    related_entity_id = models.UUIDField(null=True, blank=True)
    # --- очередь отправки --------------------------------------------------
    attempts = models.IntegerField(db_default=0)
    # Когда строку можно взять снова. NULL у PENDING означает «прямо сейчас».
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    # Момент захвата строки отправщиком. FOR UPDATE снимается при падении
    # процесса, а статус RUNNING — нет: без этой отметки такая строка
    # осталась бы в RUNNING навсегда, и её некому было бы переотправить.
    locked_at = models.DateTimeField(null=True, blank=True)
    # Ключ повтора: два одинаковых события дают одну строку, а не две
    # одинаковых записи в чате сотрудника.
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "notifications"
        verbose_name = "уведомление"
        verbose_name_plural = "уведомления"
        constraints = [
            status_check("channel", NOTIFICATION_CHANNELS, "ck_notifications_channel"),
            status_check("status", NOTIFICATION_STATUSES, "ck_notifications_status"),
            raw_check("attempts >= 0", "ck_notifications_attempts_non_negative"),
            raw_check(
                "status <> 'RUNNING' OR locked_at IS NOT NULL",
                "ck_notifications_running_is_locked",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uq_notifications_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_notifications_organization_id"
            ),
            models.Index(
                fields=["employee", "created_at"],
                name="ix_notifications_employee_created",
            ),
            # Отправщик выбирает только то, что ещё не отправлено. Частичный
            # индекс держит в себе очередь, а не всю историю уведомлений.
            models.Index(
                fields=["scheduled_at"],
                condition=models.Q(status="PENDING"),
                name="ix_notifications_pending",
            ),
            # Отправщик упорядочивает очередь по next_attempt_at, а не по
            # scheduled_at: индекс выше такому запросу не помогает вовсе.
            models.Index(
                fields=["next_attempt_at"],
                condition=models.Q(status="PENDING"),
                name="ix_notifications_next_attempt",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.notification_type} -> {self.employee_id}"
