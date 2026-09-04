"""Отправщик уведомлений: забирает очередь у backend и шлёт в Telegram.

Очередь живёт в PostgreSQL, но подключения к базе у бота нет и не будет.
Он спрашивает backend по HTTP с общим секретом, получает готовую пачку
и отчитывается о результате. Захват строк — `SELECT ... FOR UPDATE SKIP
LOCKED` — делает backend: там есть транзакция, здесь её нет.

Что делает этот цикл и чего не делает:

  * НЕ решает, кому отправлять. `chat_id` приходит из backend, где он
    взят из текущей привязки сотрудника. Бот не может ошибиться адресатом,
    потому что адресата не выбирает;
  * НЕ решает, что отправлять. Текст приходит готовым;
  * НЕ повторяет сам. Неудача сообщается backend, а он решает, ждать
    и сколько.

Результат сообщается пачкой, а не по одному: между отправкой и отчётом
процесс может упасть, и чем меньше таких промежутков, тем меньше сообщений
уйдёт дважды. Совсем избежать этого нельзя — «отправить» и «записать, что
отправили» находятся в разных системах, — но повторную отправку человек
переживёт легче, чем неотправленную.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.config.settings import settings

logger = logging.getLogger("humotech.notifications")


async def run_worker(bot: Bot, client: SelfServiceClient) -> None:
    """Бесконечный цикл. Отменяется вместе с ботом."""
    interval = max(settings.notifications_poll_seconds, 1)
    logger.info("notification worker started, interval=%ss", interval)

    while True:
        try:
            await tick(bot, client)
        except asyncio.CancelledError:
            raise
        except ApiError as error:
            # Backend недоступен. Это не повод останавливать цикл: он
            # вернётся, а очередь никуда не денется.
            logger.warning("outbox unavailable: %s", error)
        except Exception:
            logger.exception("notification worker tick failed")
        await asyncio.sleep(interval)


async def tick(bot: Bot, client: SelfServiceClient) -> int:
    """Один проход: забрать, отправить, отчитаться. Возвращает число писем."""
    batch = await client.claim_notifications()
    messages = batch.get("messages") or []
    if not messages:
        return 0

    results = []
    for item in messages:
        results.append(await _deliver(bot, item))

    await client.report_notifications(results)
    return len(messages)


async def _deliver(bot: Bot, item: dict) -> dict:
    try:
        await bot.send_message(item["chat_id"], item["text"])
        return {"id": item["id"], "sent": True}
    except TelegramForbiddenError:
        # Человек заблокировал бота или удалил чат. Повторять бессмысленно —
        # но и молча терять нельзя: причина уходит в backend, где по ней
        # видно, почему уведомление не дошло.
        return {"id": item["id"], "sent": False, "error": "blocked_by_user"}
    except TelegramAPIError as error:
        # Только класс ошибки, не текст: ответ Telegram может содержать эхо
        # запроса, то есть само уведомление целиком.
        logger.info("send failed: %s", type(error).__name__)
        return {
            "id": item["id"],
            "sent": False,
            "error": type(error).__name__,
        }


__all__ = ["run_worker", "tick"]
