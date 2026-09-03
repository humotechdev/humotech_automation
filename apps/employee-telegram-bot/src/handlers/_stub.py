"""Фабрика заглушек для разделов, которые ждут backend-api.

Каждый такой роутер заменяется настоящим по образцу handlers/vacation/router.py,
как только соответствующий эндпоинт из контракта появится на бэкенде.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.messages import ru
from src.utils.filters import IsLinked


def make_stub_router(name: str, command: str, button: str, title: str) -> Router:
    router = Router(name=name)
    router.message.filter(IsLinked())

    @router.message(Command(command))
    @router.message(F.text == button)
    async def _not_ready(message: Message) -> None:
        await message.answer(ru.IN_PROGRESS.format(name=title))

    return router
