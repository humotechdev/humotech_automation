"""Записи о событиях безопасности, у которых нет «изменённой сущности».

Сейчас это вход в CRM. Функция для зоны входа (`accounts`): она решает,
КОГДА писать, а здесь решено, ЧТО и как. Ни пароля, ни введённого
логина в запись не попадает — только исход и короткий код причины.

Неудачная попытка по несуществующей почте в журнал организации не
пишется: организации у неё нет, а заводить запись «на всех» значило бы
показывать одной компании попытки подбора в другой. Такие попытки
видны в логе сервера (без самой почты) и в счётчике ограничения входа.
"""

from __future__ import annotations

import ipaddress
import logging

from django.utils import timezone

from humotech.audit.models import AuditLog

logger = logging.getLogger("humotech.audit")

#: Коды причин — короткие и заранее известные. Произвольный текст сюда
#: не принимается: в нём легко окажется введённый пароль.
LOGIN_REASONS = frozenset(
    {
        "bad_password", "unknown", "inactive", "locked", "throttled",
        "mfa_required", "mfa_failed", "logout", "ok",
    }
)


def _ip(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return None


def record_login(
    *,
    user,
    success: bool,
    reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog | None:
    """Записать попытку входа пользователя CRM.

    `user` — найденная учётная запись или None. Без учётной записи
    возвращается None и пишется только строка лога без почты.
    """
    code = reason if reason in LOGIN_REASONS else ("ok" if success else "unknown")
    if user is None:
        logger.info("login failed for unknown account (reason=%s)", code)
        return None
    return AuditLog.objects.create(
        organization_id=user.organization_id,
        actor_user_id=user.id,
        action="auth.login.succeeded" if success else "auth.login.failed",
        entity_type="users",
        entity_id=user.id,
        new_values={"reason": code} if not success else None,
        ip_address=_ip(ip_address),
        user_agent=(user_agent or None) and user_agent[:1000],
        occurred_at=timezone.now(),
    )


__all__ = ["LOGIN_REASONS", "record_login"]
