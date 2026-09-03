"""Кэш ответов.

Инвалидация построена на РЕВИЗИИ, а не на удалении ключей: номер
`organizations.knowledge_revision` входит в сам ключ. Публикация нового
правила увеличивает счётчик — и весь прежний кэш перестаёт находиться,
без обхода Redis и без риска пропустить ключ.

Сейчас реализация in-memory; Redis подключается заменой одной строки
в `build_cache`, интерфейс менять не придётся.

Персональные ответы (часы, отметки, больничные, отпуска) не кэшируются
никогда — данные меняются в течение дня.
"""

from __future__ import annotations

import hashlib
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from humotech.ai_assistant.config import AiSettings, ai_settings


@dataclass(frozen=True)
class CacheKeyParts:
    """Всё, что влияет на ответ, обязано влиять и на ключ."""

    normalized_question_hash: str
    language: str
    office_id: str
    region_id: str
    scope_fingerprint: str
    knowledge_revision: int
    prompt_version: str
    model: str

    def build(self) -> str:
        raw = "|".join(
            (
                self.normalized_question_hash,
                self.language,
                self.office_id,
                self.region_id,
                self.scope_fingerprint,
                str(self.knowledge_revision),
                self.prompt_version,
                self.model,
            )
        )
        return "ai:answer:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CacheService(ABC):
    @abstractmethod
    def get(self, key: str) -> dict | None: ...

    @abstractmethod
    def set(self, key: str, value: dict, ttl_seconds: int) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...


class InMemoryCacheService(CacheService):
    """Кэш в памяти процесса. Потокобезопасен, с TTL.

    Для одного процесса этого достаточно. При нескольких воркерах кэш
    у каждого свой — это не ошибка, а лишь меньший процент попаданий:
    корректность обеспечивает ревизия в ключе.
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        self._data: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                self._data.pop(key, None)
                return None
            return dict(value)

    def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            if len(self._data) >= self._max_entries:
                self._evict_expired_locked()
            if len(self._data) >= self._max_entries:
                # всё ещё тесно — выкидываем самую раннюю по сроку запись
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[key] = (time.monotonic() + ttl_seconds, dict(value))

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        for key in [k for k, (exp, _) in self._data.items() if exp < now]:
            self._data.pop(key, None)


class NullCacheService(CacheService):
    """Кэш, который ничего не помнит. Для тестов и для отключённого кэша."""

    def get(self, key: str) -> dict | None:
        return None

    def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        return None

    def delete(self, key: str) -> None:
        return None

    def clear(self) -> None:
        return None


_default_cache: CacheService | None = None


def build_cache(settings: AiSettings | None = None) -> CacheService:
    """Фабрика. Redis добавится сюда одной веткой, интерфейс не изменится."""
    global _default_cache
    settings = settings or ai_settings

    backend = (settings.ai_cache_backend or "memory").lower()
    if backend == "none":
        return NullCacheService()
    if backend == "redis":
        # Redis в проекте пока не разворачивается. Явная ошибка лучше,
        # чем молчаливый откат на память: иначе в проде окажется кэш,
        # который не виден соседним воркерам, и никто об этом не узнает.
        raise NotImplementedError(
            "AI_CACHE_BACKEND=redis: RedisCacheService ещё не подключён. "
            "Используйте memory или добавьте реализацию в этот модуль."
        )

    if _default_cache is None:
        _default_cache = InMemoryCacheService()
    return _default_cache
