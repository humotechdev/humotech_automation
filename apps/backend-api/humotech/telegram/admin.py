"""Привязки Telegram в аварийном интерфейсе — только просмотр.

Подтверждать и отключать привязку отсюда нельзя намеренно. Это решение
о доступе человека к своим данным: оно принимается в CRM, где проверяются
доменные права, считается область видимости и пишется журнал. Кнопка
в админке обошла бы всё три и не оставила следа о том, кто её нажал.

`token_hash` в списке полей отсутствует. Открытой ссылки из него не
восстановить, но и показывать его незачем: пользы ноль, а на снимке экрана
он окажется первым.
"""

from __future__ import annotations

from django.contrib import admin

from humotech.core.admin import ReadOnlyAdmin
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation


@admin.register(TelegramAccount)
class TelegramAccountAdmin(ReadOnlyAdmin):
    list_display = (
        "employee", "status", "telegram_user_id", "telegram_username",
        "connected_at", "revoked_at",
    )
    list_filter = ("status",)
    search_fields = ("telegram_username", "telegram_user_id")
    fields = (
        "organization", "employee", "status", "telegram_user_id",
        "telegram_chat_id", "telegram_username", "language_code",
        "connected_at", "last_interaction_at", "revoked_at",
        "created_at", "updated_at",
    )


@admin.register(TelegramLinkInvitation)
class TelegramLinkInvitationAdmin(ReadOnlyAdmin):
    list_display = (
        "employee", "status", "expires_at", "used_at",
        "consumed_by_telegram_user_id", "created_at",
    )
    list_filter = ("status",)
    fields = (
        "organization", "employee", "status", "expires_at", "used_at",
        "revoked_at", "reviewed_at", "consumed_by_telegram_user_id",
        "created_by_user", "reviewed_by_user", "created_at", "updated_at",
    )
