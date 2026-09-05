"""Список команд бота в меню Telegram.

Один список на всех. Персональные наборы через `BotCommandScopeChat` здесь
не нужны: разделения на роли в этом боте больше нет — он сотруднический,
а HR работает в CRM.

Команды дублируют кнопки намеренно. Кнопки удобнее, но команду можно
набрать, не листая клавиатуру, и она работает из строки поиска Telegram.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="menu", description="Главное меню"),
    # Обе команды ведут в Mini App inline-кнопкой. Обычно они не нужны:
    # то же самое делают кнопки нижней клавиатуры, и на одно нажатие
    # короче. Нужны они там, где клавиатуру свернули или где запуск
    # из неё почему-то не сработал.
    BotCommand(command="scan", description="Отметиться по QR"),
    BotCommand(command="cabinet", description="Личный кабинет"),
    BotCommand(command="keyboard", description="Вернуть кнопки внизу"),
    BotCommand(command="status", description="Я сейчас в офисе?"),
    BotCommand(command="today", description="Сегодня"),
    BotCommand(command="week", description="За неделю"),
    BotCommand(command="month", description="За месяц"),
    BotCommand(command="history", description="История посещений"),
    BotCommand(command="sick_leave", description="Больничный"),
    BotCommand(command="vacation", description="Отпуск"),
    BotCommand(command="requests", description="Мои заявки"),
    BotCommand(command="help", description="Помощь"),
]


async def set_default_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeDefault())
    except Exception:
        # Меню команд — удобство, а не работоспособность. Упасть на старте
        # из-за него значило бы не запустить бота целиком.
        logger.exception("failed to set bot commands")


__all__ = ["COMMANDS", "set_default_commands"]
