"""Ограничение частоты обращений одного сотрудника.

Защищает и от случайного шквала (сотрудник зажал кнопку), и от намеренного
слива бюджета на токены. Счётчики в памяти процесса: этого достаточно, пока
воркер один; при нескольких — лимит станет мягче, но не исчезнет.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

from humotech.ai_assistant.errors import RateLimitedError


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    reason: str | None = None


class RateLimiter:
    def __init__(self, *, per_minute: int, per_day: int) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self._events: dict[uuid.UUID, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, employee_id: uuid.UUID) -> RateLimitDecision:
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(employee_id, deque())
            # чистим всё старше суток
            while events and now - events[0] > 86_400:
                events.popleft()

            minute_count = sum(1 for ts in events if now - ts <= 60)
            if minute_count >= self.per_minute:
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=60,
                    reason="Слишком много вопросов за минуту",
                )
            if len(events) >= self.per_day:
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=3600,
                    reason="Исчерпан дневной лимит вопросов",
                )
            events.append(now)
            return RateLimitDecision(allowed=True)

    def enforce(
        self, employee_id: uuid.UUID, *, organization_id: uuid.UUID | None = None
    ) -> None:
        decision = self.check(employee_id)
        if not decision.allowed:
            raise RateLimitedError(decision.reason or "Превышен лимит запросов")

    def reset(self, employee_id: uuid.UUID | None = None) -> None:
        with self._lock:
            if employee_id is None:
                self._events.clear()
            else:
                self._events.pop(employee_id, None)


#: Попытки, которые не стоили ничего и в лимит не идут: отказ по лимиту
#: (иначе поток отказов продлевал бы блокировку бесконечно) и отказ по
#: длине или пустоте вопроса — до любых обращений к провайдеру.
_FREE_ATTEMPTS = ("rate_limited", "question_rejected", "employee_not_found")


class DatabaseRateLimiter:
    """Лимит по журналу `llm_query_logs` — общий для всех воркеров.

    Счётчики в памяти процесса (`RateLimiter`) при gunicorn×N дают лимит ×N:
    каждый процесс считает только свои запросы, и дневной бюджет на токены
    фактически умножается на число воркеров. Журнал же один на всех: каждая
    попытка и так пишется в него в `AnswerService._finish`, поэтому
    посчитать строки сотрудника за минуту и за сутки — честный общий счёт
    без новой таблицы и без зависимости от того, какой настроен кэш.

    Остаётся гонка «проверил — записал»: одновременные запросы одного
    сотрудника проверяются до того, как любой из них запишется. Она
    ограничена числом параллельных запросов и не накапливается: следующая
    же проверка видит все записанные строки.
    """

    def __init__(self, *, per_minute: int, per_day: int) -> None:
        self.per_minute = per_minute
        self.per_day = per_day

    def check(
        self, employee_id: uuid.UUID, *, organization_id: uuid.UUID | None = None
    ) -> RateLimitDecision:
        from datetime import datetime, timedelta, timezone

        from django.db.models import Count, Q

        from humotech.ai_assistant.models import LlmQueryLog

        now = datetime.now(tz=timezone.utc)
        rows = LlmQueryLog.objects.filter(
            employee_id=employee_id,
            created_at__gte=now - timedelta(days=1),
        ).exclude(error_code__in=_FREE_ATTEMPTS)
        if organization_id is not None:
            # Индекс журнала начинается с организации: без неё запрос
            # просматривал бы сутки всех организаций.
            rows = rows.filter(organization_id=organization_id)
        counts = rows.aggregate(
            day=Count("id"),
            minute=Count("id", filter=Q(created_at__gte=now - timedelta(minutes=1))),
        )
        if counts["minute"] >= self.per_minute:
            return RateLimitDecision(
                allowed=False, retry_after_seconds=60,
                reason="Слишком много вопросов за минуту",
            )
        if counts["day"] >= self.per_day:
            return RateLimitDecision(
                allowed=False, retry_after_seconds=3600,
                reason="Исчерпан дневной лимит вопросов",
            )
        return RateLimitDecision(allowed=True)

    def enforce(
        self, employee_id: uuid.UUID, *, organization_id: uuid.UUID | None = None
    ) -> None:
        decision = self.check(employee_id, organization_id=organization_id)
        if not decision.allowed:
            raise RateLimitedError(decision.reason or "Превышен лимит запросов")

    def reset(self, employee_id: uuid.UUID | None = None) -> None:
        """Счёт живёт в журнале; сбрасывать в памяти нечего."""
        return None
