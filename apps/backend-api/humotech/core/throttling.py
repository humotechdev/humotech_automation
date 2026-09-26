"""Ограничение частоты с общим для всех воркеров счётчиком.

Почему не встроенные классы DRF как есть:

* они считают в кэше Django, а у нас это LocMem — свой в каждом процессе.
  При gunicorn×3 предел «5 в минуту» на деле становится «15 в минуту»,
  а запросы, разбросанные по воркерам, счётчик не видит вовсе;
* их счётчик — «прочитать историю, дописать, сохранить» без блокировки.
  Сто параллельных запросов читают одну и ту же историю и проходят все.

Здесь счётчик — строка в PostgreSQL (`auth_rate_counters`), и меняется она
под `SELECT ... FOR UPDATE`: попытка сначала засчитывается, потом
выполняется. Параллельные запросы выстраиваются в очередь на строке, и
предел не обходится ни числом воркеров, ни одновременностью.

Окно фиксированное: первая попытка открывает окно, в нём копятся попытки,
по истечении окна счёт начинается заново. Превышение предела ставит
блокировку на `lock_seconds`; пока она стоит, попытки отклоняются и
не удлиняют её — иначе человек, который жмёт «войти» раз в минуту,
никогда бы не дождался разблокировки.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle

# Как часто между делом вычищать старые строки: раз в столько обращений
# в среднем. Отдельной периодической задачи ради таблицы из сотен строк
# заводить незачем.
_CLEANUP_ONE_IN = 50
_KEEP_STALE = timedelta(days=1)


@dataclass(frozen=True)
class Limit:
    """Предел: не больше `hits` попыток за `window_seconds`."""

    hits: int
    window_seconds: int
    lock_seconds: int


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    retry_after: int = 0
    #: Эта попытка и поставила блокировку. Нужен, чтобы записать в журнал
    #: событие один раз, а не на каждую отклонённую попытку.
    just_locked: bool = False


def counter_key(*parts: object) -> str:
    """Ключ строки: хеш от частей. Логины и адреса открытым текстом не лежат."""
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()


def hit(key: str, limit: Limit, *, now: datetime | None = None) -> Verdict:
    """Засчитать попытку и сказать, разрешена ли она."""
    from humotech.accounts.models import AuthRateCounter

    now = now or timezone.now()
    window = timedelta(seconds=limit.window_seconds)

    with transaction.atomic():
        AuthRateCounter.objects.bulk_create(
            [AuthRateCounter(key=key, hits=0, window_started_at=now,
                             updated_at=now)],
            ignore_conflicts=True,
        )
        row = AuthRateCounter.objects.select_for_update().get(key=key)

        if row.locked_until is not None and row.locked_until > now:
            return Verdict(False, _seconds(row.locked_until - now))

        if row.locked_until is not None or row.window_started_at + window <= now:
            # Блокировка отбыта или окно истекло — счёт с нуля.
            row.hits = 0
            row.window_started_at = now
            row.locked_until = None

        row.hits += 1
        row.updated_at = now
        verdict = Verdict(True)
        if row.hits > limit.hits:
            row.locked_until = now + timedelta(seconds=limit.lock_seconds)
            verdict = Verdict(False, limit.lock_seconds, just_locked=True)
        row.save()

    if random.randrange(_CLEANUP_ONE_IN) == 0:
        _cleanup(now)
    return verdict


def peek(key: str, *, now: datetime | None = None) -> int:
    """Сколько секунд ещё стоит блокировка (0 — не стоит). Ничего не меняет."""
    from humotech.accounts.models import AuthRateCounter

    now = now or timezone.now()
    locked_until = (
        AuthRateCounter.objects.filter(key=key, locked_until__gt=now)
        .values_list("locked_until", flat=True)
        .first()
    )
    return _seconds(locked_until - now) if locked_until else 0


def release(key: str) -> None:
    """Вернуть одну попытку: она оказалась законной."""
    from humotech.accounts.models import AuthRateCounter

    AuthRateCounter.objects.filter(key=key, locked_until__isnull=True).update(
        hits=Greatest(F("hits") - 1, 0)
    )


def reset(*keys: str) -> None:
    """Забыть счётчики целиком (удачный вход, смена пароля администратором)."""
    from humotech.accounts.models import AuthRateCounter

    if keys:
        AuthRateCounter.objects.filter(key__in=keys).delete()


def _cleanup(now: datetime) -> None:
    from django.db.models import Q

    from humotech.accounts.models import AuthRateCounter

    AuthRateCounter.objects.filter(
        Q(locked_until__isnull=True) | Q(locked_until__lt=now),
        updated_at__lt=now - _KEEP_STALE,
    ).delete()


def _seconds(delta: timedelta) -> int:
    return max(1, math.ceil(delta.total_seconds()))


class SharedCounterMixin:
    """Пределы DRF (`rate`, `scope`, ключ) на общем счётчике вместо кэша.

    Ставится перед классом DRF: разбор `"20/min"` и выбор ключа остаются
    его, а считает `hit()` — один счётчик на все воркеры, без гонки
    «прочитать-дописать». Превысивший предел ждёт одно окно целиком.
    """

    def allow_request(self, request, view):
        if self.rate is None:
            return True
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True
        verdict = hit(
            counter_key("drf", self.key),
            Limit(self.num_requests, self.duration, self.duration),
        )
        self._retry_after = verdict.retry_after
        return verdict.allowed

    def wait(self):
        return getattr(self, "_retry_after", None) or None


class SharedSimpleRateThrottle(SharedCounterMixin, SimpleRateThrottle):
    pass


class SharedScopedRateThrottle(SharedCounterMixin, ScopedRateThrottle):
    def allow_request(self, request, view):
        # Предел берётся из `throttle_scope` view — как у ScopedRateThrottle,
        # который узнаёт его только здесь, а не в конструкторе.
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


__all__ = [
    "Limit", "SharedCounterMixin", "SharedScopedRateThrottle",
    "SharedSimpleRateThrottle", "Verdict", "counter_key", "hit", "peek",
    "release", "reset",
]
