"""Хранилище персональных токенов сотрудников: telegram_id -> access_token.

Сейчас — в памяти процесса. Для продакшена нужна замена на Redis: при рестарте
бота все привязки теряются и людям придётся заново проходить /start.
Интерфейс специально минимальный, чтобы подмена была в одном месте.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TokenStorage(ABC):
    @abstractmethod
    async def get(self, telegram_id: int) -> str | None: ...

    @abstractmethod
    async def set(self, telegram_id: int, token: str) -> None: ...

    @abstractmethod
    async def delete(self, telegram_id: int) -> None: ...


class MemoryTokenStorage(TokenStorage):
    def __init__(self) -> None:
        self._data: dict[int, str] = {}

    async def get(self, telegram_id: int) -> str | None:
        return self._data.get(telegram_id)

    async def set(self, telegram_id: int, token: str) -> None:
        self._data[telegram_id] = token

    async def delete(self, telegram_id: int) -> None:
        self._data.pop(telegram_id, None)
