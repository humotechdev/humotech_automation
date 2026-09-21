"""Точка входа бота HUMOTECH.

Запуск:  python -m src.main   (из apps/employee-telegram-bot)

Два дела одновременно: отвечает на сообщения и разгребает очередь
уведомлений. Второе — отдельная задача рядом с опросом Telegram, а не
отдельный процесс: у них общая сессия к Telegram и общий клиент к backend,
и разводить их значило бы держать вдвое больше соединений ради ничего.

Токенов сотрудников здесь нет ни в каком виде. Хранилища для них тоже нет:
кто пишет боту, спрашивается у backend на каждом обновлении. Отзыв привязки
действует со следующего нажатия кнопки, а не по истечении чего-либо.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, ErrorEvent, Message

from src.api.errors import ApiError, Forbidden, Unauthorized
from src.api.selfservice import SelfServiceClient
from src.config.settings import settings
from src.handlers import build_root_router
from src.messages import employee as text
from src.middlewares.employee import EmployeeMiddleware
from src.notifications.sender import TelegramSender, build_stub_sender
from src.notifications.worker import run_worker
from src.utils.commands import set_default_commands
from src.utils.menu_button import ensure_menu_button

logger = logging.getLogger("humotech.bot")


def _target_message(event: ErrorEvent) -> Message | None:
    update = event.update
    if getattr(update, "message", None):
        return update.message
    callback: CallbackQuery | None = getattr(update, "callback_query", None)
    return callback.message if callback else None


async def on_error(event: ErrorEvent) -> bool:
    """Ни одна ошибка не должна оставить человека без ответа."""
    exc = event.exception
    message = _target_message(event)

    if isinstance(exc, (Unauthorized, Forbidden)):
        answer = text.NO_ACCESS
    elif isinstance(exc, ApiError):
        answer = text.BACKEND_DOWN
    else:
        logger.exception("unhandled error: %s", exc)
        answer = text.BACKEND_DOWN

    if message is not None:
        try:
            await message.answer(answer)
        except Exception:
            logger.exception("failed to deliver error message")
    return True


async def run_queue_only() -> None:
    """Изолированный стенд: одна очередь и заглушка вместо Telegram.

    Диспетчер здесь не поднимается вовсе, и объект `Bot` не создаётся.
    Это не бережливость: живой `Bot` начинает опрашивать Telegram сразу,
    и «мы же подменили отправщик» перестало бы что-либо значить — процесс
    всё равно ходил бы наружу с настоящим токеном.
    """
    sender = build_stub_sender()
    client = SelfServiceClient()
    logger.info(
        "queue-only mode: sender=stub, api=%s", settings.backend_api_url
    )
    try:
        await run_worker(sender, client)
    finally:
        await client.close()


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if settings.notifications_sender == "stub":
        await run_queue_only()
        return

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    client = SelfServiceClient()

    # outer_middleware на update — выполняется ДО фильтров роутеров,
    # поэтому хендлеры уже видят профиль или причину отказа.
    dispatcher.update.outer_middleware(EmployeeMiddleware(client))
    dispatcher.include_router(build_root_router())
    dispatcher.errors.register(on_error)

    me = await bot.get_me()
    await set_default_commands(bot)
    await ensure_menu_button(bot)
    logger.info("bot @%s started, api=%s", me.username, settings.backend_api_url)

    # Клиент отдаётся отправщику: заявление на больничный приходит
    # человеку файлом, а файл собирается из заявки на каждое обращение.
    worker = asyncio.create_task(run_worker(TelegramSender(bot, client), client))
    try:
        await dispatcher.start_polling(bot)
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        await client.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("bot stopped")
