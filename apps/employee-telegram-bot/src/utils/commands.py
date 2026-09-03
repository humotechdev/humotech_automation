"""Персональное меню команд для конкретного чата.

Telegram позволяет задать разный список команд каждому пользователю через
setMyCommands со scope=BotCommandScopeChat. Благодаря этому сотрудник физически
не видит HR-команд в меню бота, хотя бот один на всех.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

logger = logging.getLogger(__name__)

GUEST_COMMANDS = [
    BotCommand(command="start", description="Начало работы и привязка аккаунта"),
]

EMPLOYEE_COMMANDS = [
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="attendance", description="Мои приходы и уходы"),
    BotCommand(command="statistics", description="Статистика за месяц"),
    BotCommand(command="vacation", description="Заявка на отпуск"),
    BotCommand(command="sick_leave", description="Оформить больничный"),
    BotCommand(command="correction", description="Исправить отметку"),
    BotCommand(command="question", description="Вопрос в HR"),
]

HR_COMMANDS = EMPLOYEE_COMMANDS + [
    BotCommand(command="hr", description="HR-панель"),
    BotCommand(command="requests", description="Заявки на согласование"),
    BotCommand(command="today", description="Кто на месте сегодня"),
]


def commands_for(role: str | None) -> list[BotCommand]:
    if role in ("hr", "admin"):
        return HR_COMMANDS
    if role == "employee":
        return EMPLOYEE_COMMANDS
    return GUEST_COMMANDS


async def set_default_commands(bot: Bot) -> None:
    """Глобальный список — то, что видит непривязанный пользователь."""
    await bot.set_my_commands(GUEST_COMMANDS, scope=BotCommandScopeDefault())


async def sync_commands_for_chat(bot: Bot, chat_id: int, role: str | None) -> None:
    """Вызывать после привязки аккаунта и при смене роли."""
    try:
        await bot.set_my_commands(
            commands_for(role), scope=BotCommandScopeChat(chat_id=chat_id)
        )
    except Exception as exc:  # меню — не критичный путь, бот должен жить дальше
        logger.warning("set_my_commands failed for chat %s: %s", chat_id, exc)
