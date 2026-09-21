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

Кого именно звать для последнего шага, решает `sender.py`, и решает
один раз при запуске. Здесь про Telegram не знают вовсе — поэтому цикл
можно провести целиком, ни разу не выйдя наружу, и это не «режим
тестирования» внутри рабочего кода, а другой отправщик снаружи него.

Результат сообщается пачкой, а не по одному: между отправкой и отчётом
процесс может упасть, и чем меньше таких промежутков, тем меньше сообщений
уйдёт дважды. Совсем избежать этого нельзя — «отправить» и «записать, что
отправили» находятся в разных системах, — но повторную отправку человек
переживёт легче, чем неотправленную.
"""

from __future__ import annotations

import asyncio
import logging

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.config.settings import settings
from src.notifications.sender import Sender

logger = logging.getLogger("humotech.notifications")


async def run_worker(sender: Sender, client: SelfServiceClient) -> None:
    """Бесконечный цикл. Отменяется вместе с ботом."""
    interval = max(settings.notifications_poll_seconds, 1)
    logger.info("notification worker started, interval=%ss", interval)

    while True:
        try:
            await tick(sender, client)
        except asyncio.CancelledError:
            raise
        except ApiError as error:
            # Backend недоступен. Это не повод останавливать цикл: он
            # вернётся, а очередь никуда не денется.
            logger.warning("outbox unavailable: %s", error)
        except Exception:
            logger.exception("notification worker tick failed")
        await asyncio.sleep(interval)


async def tick(sender: Sender, client: SelfServiceClient) -> int:
    """Один проход: забрать, отправить, отчитаться. Возвращает число писем."""
    batch = await client.claim_notifications()
    messages = batch.get("messages") or []
    if not messages:
        return 0

    results = []
    for item in messages:
        results.append(await _deliver(sender, item))

    await client.report_notifications(results)
    return len(messages)


async def _deliver(sender: Sender, item: dict) -> dict:
    """Один отчёт для backend. Кто именно относил — решено при запуске.

    Причина неудачи наружу уходит кодом, а не текстом: ответ Telegram
    может содержать эхо запроса, то есть само уведомление целиком.
    """
    outcome = await sender.deliver(
        chat_id=item["chat_id"],
        text=item["text"],
        notification_type=item.get("type") or "",
        # На что ссылается уведомление. Нужно там, где к сообщению
        # полагается кнопка: приглашение на опрос без этого открывало бы
        # «какой-то» опрос.
        entity_id=item.get("entity_id"),
    )
    if outcome.sent:
        return {"id": item["id"], "sent": True}
    return {"id": item["id"], "sent": False, "error": outcome.error}


__all__ = ["run_worker", "tick"]
