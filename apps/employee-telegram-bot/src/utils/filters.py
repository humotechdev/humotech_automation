"""Фильтры ролей. Вешаются на роутер целиком, а не на каждый хендлер."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

HR_ROLES = ("hr", "admin")


class RoleFilter(BaseFilter):
    """Пропускает апдейт, только если роль из GET /employees/me входит в список."""

    def __init__(self, *roles: str) -> None:
        self.roles = roles

    async def __call__(self, event: TelegramObject, **data) -> bool:
        return data.get("role") in self.roles


class IsLinked(BaseFilter):
    """Аккаунт привязан к сотруднику."""

    async def __call__(self, event: TelegramObject, **data) -> bool:
        return data.get("role") is not None


class IsNotLinked(BaseFilter):
    async def __call__(self, event: TelegramObject, **data) -> bool:
        return data.get("role") is None
