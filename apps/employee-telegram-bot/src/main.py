"""Точка входа бота HUMOTECH.

Запуск:  python -m src.main   (из apps/employee-telegram-bot)

Один бот на сотрудников и HR. Что видит человек, определяет роль из
GET /employees/me; реальные права проверяет backend-api.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, ErrorEvent, Message

from src.api import MemoryTokenStorage, build_client
from src.api.errors import ApiError, Forbidden, Unauthorized
from src.config.settings import settings
from src.handlers import build_root_router
from src.messages import ru
from src.middlewares.auth import AuthMiddleware
from src.utils.commands import set_default_commands

logger = logging.getLogger("humotech.bot")


def _target_message(event: ErrorEvent) -> Message | None:
    update = event.update
    if getattr(update, "message", None):
        return update.message
    callback: CallbackQuery | None = getattr(update, "callback_query", None)
    return callback.message if callback else None


async def on_error(event: ErrorEvent) -> bool:
    """Ни одна ошибка не должна оставить пользователя без ответа."""
    exc = event.exception
    message = _target_message(event)

    if isinstance(exc, Unauthorized):
        text = ru.ERR_SESSION_EXPIRED
    elif isinstance(exc, Forbidden):
        text = ru.ERR_FORBIDDEN
    elif isinstance(exc, ApiError):
        text = exc.message
    else:
        logger.exception("unhandled error: %s", exc)
        text = ru.ERR_SERVER

    if message is not None:
        try:
            await message.answer(text)
        except Exception:
            logger.exception("failed to deliver error message")
    return True


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())

    client = build_client()
    tokens = MemoryTokenStorage()

    # outer_middleware на update — выполняется ДО фильтров роутеров,
    # поэтому RoleFilter уже видит роль в data
    dispatcher.update.outer_middleware(AuthMiddleware(client, tokens))
    dispatcher.include_router(build_root_router())
    dispatcher.errors.register(on_error)

    me = await bot.get_me()
    await set_default_commands(bot)
    logger.info("bot @%s started, api_mode=%s", me.username, settings.api_mode)

    try:
        await dispatcher.start_polling(bot)
    finally:
        await client.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("bot stopped")
