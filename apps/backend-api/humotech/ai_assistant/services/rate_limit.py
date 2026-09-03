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

    def enforce(self, employee_id: uuid.UUID) -> None:
        decision = self.check(employee_id)
        if not decision.allowed:
            raise RateLimitedError(decision.reason or "Превышен лимит запросов")

    def reset(self, employee_id: uuid.UUID | None = None) -> None:
        with self._lock:
            if employee_id is None:
                self._events.clear()
            else:
                self._events.pop(employee_id, None)
