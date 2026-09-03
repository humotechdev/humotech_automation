"""Уведомления сотрудникам: Telegram, почта, push, внутри интерфейса."""

from __future__ import annotations

from django.db import models

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

    class Meta:
        db_table = "notifications"
        verbose_name = "уведомление"
        verbose_name_plural = "уведомления"
        constraints = [
            status_check("channel", NOTIFICATION_CHANNELS, "ck_notifications_channel"),
            status_check("status", NOTIFICATION_STATUSES, "ck_notifications_status"),
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
        ]

    def __str__(self) -> str:
        return f"{self.notification_type} -> {self.employee_id}"
