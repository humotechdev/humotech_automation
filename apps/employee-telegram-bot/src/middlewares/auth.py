"""Определяет, кто пишет боту, и кладёт в data токен, профиль и роль.

Роль НИКОГДА не вычисляется на стороне бота — она приходит из GET /employees/me.
Всё, на что она влияет здесь, — какие кнопки и роутеры показать. Реальную
проверку прав делает бэкенд (правило №3 архитектуры).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from src.api import BackendClient, TokenStorage
from src.api.errors import ApiError, Unauthorized

logger = logging.getLogger(__name__)

PROFILE_TTL_SECONDS = 60


class AuthMiddleware(BaseMiddleware):
    def __init__(self, client: BackendClient, tokens: TokenStorage) -> None:
        self.client = client
        self.tokens = tokens
        self._cache: dict[int, tuple[float, dict]] = {}

    def invalidate(self, telegram_id: int) -> None:
        self._cache.pop(telegram_id, None)

    async def _profile(self, telegram_id: int, token: str) -> dict:
        cached = self._cache.get(telegram_id)
        if cached and time.monotonic() - cached[0] < PROFILE_TTL_SECONDS:
            return cached[1]
        profile = await self.client.me(token)
        self._cache[telegram_id] = (time.monotonic(), profile)
        return profile

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")

        data["client"] = self.client
        data["tokens"] = self.tokens
        data["auth"] = self
        data["token"] = None
        data["employee"] = None
        data["role"] = None

        if user is not None:
            token = await self.tokens.get(user.id)
            if token:
                try:
                    profile = await self._profile(user.id, token)
                    data["token"] = token
                    data["employee"] = profile
                    data["role"] = profile.get("role")
                except Unauthorized:
                    # токен отозван или протух — стираем, пользователь пройдёт /start
                    await self.tokens.delete(user.id)
                    self.invalidate(user.id)
                except ApiError as exc:
                    # бэкенд недоступен: пускаем дальше без роли, хендлер покажет ошибку
                    logger.warning("profile fetch failed for %s: %s", user.id, exc)

        return await handler(event, data)
