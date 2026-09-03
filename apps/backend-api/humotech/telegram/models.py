"""Привязка Telegram-аккаунта к сотруднику.

Именно отсюда бот узнаёт, КТО сканирует QR: сотрудник определяется по
привязанному Telegram-аккаунту, а не по данным, присланным клиентом.
"""

from __future__ import annotations

from django.db import models

from humotech.core.enums import TELEGRAM_ACCOUNT_STATUSES, choices, status_check
from humotech.core.models import (
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class TelegramAccount(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="telegram_account",
    )
    telegram_user_id = models.BigIntegerField()
    telegram_chat_id = models.BigIntegerField()
    telegram_username = models.CharField(max_length=255, null=True, blank=True)
    language_code = models.CharField(max_length=10, db_default="ru")
    status = models.CharField(
        max_length=20, choices=choices(TELEGRAM_ACCOUNT_STATUSES)
    )
    connected_at = models.DateTimeField()
    last_interaction_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "telegram_accounts"
        verbose_name = "привязка Telegram"
        verbose_name_plural = "привязки Telegram"
        constraints = [
            # Один сотрудник — один Telegram, один Telegram — один сотрудник.
            models.UniqueConstraint(
                fields=["employee"], name="uq_telegram_accounts_employee"
            ),
            models.UniqueConstraint(
                fields=["telegram_user_id"], name="uq_telegram_accounts_tg_user"
            ),
            status_check(
                "status", TELEGRAM_ACCOUNT_STATUSES, "ck_telegram_accounts_status"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_telegram_accounts_organization_id"
            ),
        ]

    def __str__(self) -> str:
        return self.telegram_username or str(self.telegram_user_id)
