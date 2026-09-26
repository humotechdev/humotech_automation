"""Защита входа в CRM: предел попыток, журнал входов, обрыв сессий.

Предел попыток
--------------
Три счётчика, у каждого своя задача:

* логин + адрес — основной. Пять неудач подряд с одного адреса по одной
  учётной записи — это уже не опечатка. Блокировка короткая и касается
  только этой пары: HR из другого офиса войдёт в ту же учётку спокойно;
* логин с любого адреса — против перебора одной учётки с многих адресов.
  Предел выше, чтобы один злоумышленник не закрывал человеку вход пятью
  запросами;
* адрес по любым логинам — против «один частый пароль по всем почтам».

Ключ строится из того, что прислал клиент (организация и логин в верхнем
регистре — так же, как их сравнивает `iexact`), а не из найденной
учётной записи. Поэтому несуществующий логин блокируется ровно так же,
как настоящий, и по ответам нельзя узнать, какие учётки существуют.

Попытка засчитывается ДО проверки пароля, а удачная — возвращается.
Иначе сотня параллельных запросов успела бы проверить сто паролей, пока
первый из них дописывает неудачу.

Адрес берётся `client_ip`: `X-Forwarded-For` учитывается ровно настолько,
насколько настроено `TRUSTED_PROXY_COUNT`, и подменой заголовка новый
счётчик не завести.
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.contrib.sessions.models import Session
from django.utils import timezone

from humotech.core import throttling
from humotech.core.clientip import client_ip
from humotech.core.rbac import Actor, AuditTrail

log = logging.getLogger("humotech.auth")

_DEFAULTS = {
    "ACCOUNT_IP": (5, 900, 900),
    "ACCOUNT": (20, 3600, 900),
    "IP": (30, 900, 900),
}


def _limit(name: str) -> throttling.Limit:
    configured = getattr(settings, "LOGIN_THROTTLE", {}).get(name)
    hits, window, lock = configured or _DEFAULTS[name]
    return throttling.Limit(int(hits), int(window), int(lock))


def _norm(value: str) -> str:
    return (value or "").strip().upper()


class LoginGuard:
    """Одна попытка входа: засчитать, узнать исход, записать в журнал."""

    def __init__(self, request, organization_code: str, login: str):
        self.request = request
        self.organization_code = organization_code
        self.login = login
        self.ip = client_ip(request) or "unknown"
        org, who = _norm(organization_code), _norm(login)
        self.keys = {
            "ACCOUNT_IP": throttling.counter_key("login:account-ip", org, who, self.ip),
            "ACCOUNT": throttling.counter_key("login:account", org, who),
            "IP": throttling.counter_key("login:ip", self.ip),
        }
        self._counted: list[str] = []

    def admit(self) -> int:
        """0 — можно проверять пароль; иначе через сколько секунд повторить."""
        for name in ("ACCOUNT_IP", "ACCOUNT", "IP"):
            verdict = throttling.hit(self.keys[name], _limit(name))
            if not verdict.allowed:
                if verdict.just_locked:
                    self._audit_lock(name, verdict.retry_after)
                return verdict.retry_after
            self._counted.append(name)
        return 0

    def succeeded(self, user) -> None:
        # Счётчики учётки забываются: человек вошёл, прошлые опечатки
        # не должны копиться до следующего раза. Счётчик адреса — нет:
        # иначе злоумышленник со своей учёткой сбрасывал бы его входом
        # между попытками. Ему возвращается только эта, законная, попытка.
        throttling.reset(self.keys["ACCOUNT_IP"], self.keys["ACCOUNT"])
        if "IP" in self._counted:
            throttling.release(self.keys["IP"])
        _record(
            Actor(user_id=user.id, organization_id=user.organization_id),
            action="auth.login",
            entity_type="users",
            entity_id=user.id,
            request=self.request,
        )

    def failed(self) -> None:
        user, organization_id = _lookup(self.organization_code, self.login)
        if organization_id is None:
            # Организации нет — журнала, куда писать, тоже нет.
            log.warning("login failed: unknown organization, ip=%s", self.ip)
            return
        _record(
            Actor(user_id=None, organization_id=organization_id),
            action="auth.login_failed",
            entity_type="users" if user else "organizations",
            entity_id=user.id if user else organization_id,
            after={"login": self.login[:100]},
            request=self.request,
        )

    def _audit_lock(self, bucket: str, seconds: int) -> None:
        log.warning("login locked: bucket=%s ip=%s for %ss", bucket, self.ip, seconds)
        user, organization_id = _lookup(self.organization_code, self.login)
        if organization_id is None:
            return
        _record(
            Actor(user_id=None, organization_id=organization_id),
            action="auth.login_locked",
            entity_type="users" if user else "organizations",
            entity_id=user.id if user else organization_id,
            after={"login": self.login[:100], "scope": bucket.lower(),
                   "seconds": seconds},
            request=self.request,
        )


def unlock_account(user) -> None:
    """Снять блокировку входа учётки со всех адресов (новый пароль от HR).

    Счётчики «логин + адрес» по отдельности не найти — ключ хеширован
    вместе с адресом, — поэтому снимается общий счётчик учётки, а
    парные истекут сами за свои пятнадцать минут.
    """
    throttling.reset(
        throttling.counter_key(
            "login:account", _norm(user.organization.code), _norm(user.email)
        )
    )


def audit_logout(request, user) -> None:
    _record(
        Actor(user_id=user.id, organization_id=user.organization_id),
        action="auth.logout",
        entity_type="users",
        entity_id=user.id,
        request=request,
    )


def end_sessions(user_id: uuid.UUID, *, keep: str | None = None) -> int:
    """Удалить на сервере все сессии пользователя (кроме `keep`).

    Сессии лежат в базе закодированными, поиска по пользователю у Django
    нет — перебираются только живые. Их немного: срок сессии — часы.
    """
    target = str(user_id)
    doomed = [
        session.session_key
        for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator()
        if session.session_key != keep
        and str(session.get_decoded().get("_auth_user_id")) == target
    ]
    if doomed:
        Session.objects.filter(session_key__in=doomed).delete()
    return len(doomed)


def _lookup(organization_code: str, login: str):
    from humotech.accounts.models import User
    from humotech.organizations.models import Organization

    organization_id = (
        Organization.objects.filter(code__iexact=organization_code)
        .values_list("id", flat=True)
        .first()
    )
    if organization_id is None:
        return None, None
    user = (
        User.objects.filter(organization_id=organization_id, email__iexact=login)
        .only("id")
        .first()
    )
    return user, organization_id


def _record(actor, *, action, entity_type, entity_id, request, after=None):
    AuditTrail().record(
        actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        after=after,
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT"),
    )


__all__ = ["LoginGuard", "audit_logout", "end_sessions", "unlock_account"]
