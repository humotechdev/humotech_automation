"""Кто именно уносит сообщение из очереди наружу.

Отправщик один — тот, что ходит в Telegram. Заглушка рядом с ним нужна
не для «удобства тестов», а потому, что проверка очереди без неё
кончается сообщениями настоящим людям: очередь, воркер и backend в
проверке участвуют по-настоящему, и единственное место, где проверку
можно честно оборвать, — последний шаг.

Выбор явный и делается один раз при запуске (`NOTIFICATIONS_SENDER`).
Ветки «если тест» внутри рабочего кода отправки нет: она означала бы,
что боевой процесс носит в себе способ не отправлять, и однажды в него
попадёт.

Заглушка не эмулирует Telegram. Она сообщает ровно то, что от
отправщика требуется очереди: удалось или нет, а если нет — код. Всё
остальное про Telegram знает `TelegramSender`, и только он.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from src.config.settings import settings

logger = logging.getLogger("humotech.notifications")

# Как выглядит настоящий токен бота: id, двоеточие, 35 символов.
REAL_TOKEN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


class Outcome:
    """Итог одной попытки. `error` пуст ровно тогда, когда `sent`."""

    __slots__ = ("sent", "error")

    def __init__(self, *, sent: bool, error: str | None = None) -> None:
        self.sent = sent
        self.error = None if sent else (error or "unknown")


class Sender(Protocol):
    async def deliver(
        self, *, chat_id: int, text: str, notification_type: str
    ) -> Outcome: ...


class TelegramSender:
    """Настоящая доставка. Единственное место, где бот пишет в чат."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def deliver(
        self, *, chat_id: int, text: str, notification_type: str
    ) -> Outcome:
        del notification_type  # Telegram про типы уведомлений не знает
        try:
            await self._bot.send_message(chat_id, text)
            return Outcome(sent=True)
        except TelegramForbiddenError:
            # Человек заблокировал бота или удалил чат. Повторять
            # бессмысленно — но и молча терять нельзя.
            return Outcome(sent=False, error="blocked_by_user")
        except TelegramAPIError as error:
            # Только класс ошибки, не текст: ответ Telegram может
            # содержать эхо запроса, то есть само уведомление.
            logger.info("send failed: %s", type(error).__name__)
            return Outcome(sent=False, error=type(error).__name__)


class StubSender:
    """Никуда не ходит. Пишет строку в журнал и отвечает очереди.

    Нужна ровно одна возможность сверх «всегда успех»: заранее названные
    типы уведомлений объявляются неудачными. Без неё нельзя проверить ни
    повтор, ни исчерпание попыток, а именно там очередь и ошибается.
    """

    def __init__(self, fail_types: tuple[str, ...] = ()) -> None:
        self._fail = tuple(part for part in fail_types if part)

    async def deliver(
        self, *, chat_id: int, text: str, notification_type: str
    ) -> Outcome:
        del text  # содержимое проверочного сообщения в журнал не идёт
        if any(notification_type.startswith(part) for part in self._fail):
            logger.info("STUB FAIL chat=%s type=%s", chat_id, notification_type)
            return Outcome(sent=False, error="stub_forced_failure")
        logger.info("STUB SEND chat=%s type=%s", chat_id, notification_type)
        return Outcome(sent=True)


class StubSenderRefused(RuntimeError):
    """Заглушку попросили работать там, где есть настоящий токен."""


def build_stub_sender() -> StubSender:
    """Заглушка — и проверка, что рядом нет настоящего токена.

    Смысл проверки не в аккуратности, а в том, что заглушка и живой
    токен в одном процессе означают: кто-то поднял тестовый стенд
    копией рабочего окружения. Один забытый `NOTIFICATIONS_SENDER` —
    и сообщения снова уходят людям.
    """
    if REAL_TOKEN.match(settings.bot_token or ""):
        raise StubSenderRefused(
            "NOTIFICATIONS_SENDER=stub, но BOT_TOKEN похож на настоящий. "
            "В тестовый процесс рабочий токен не передают: заглушка "
            "защищает только последний шаг, а всё остальное в этом "
            "процессе — настоящее."
        )
    fail = tuple(
        part.strip()
        for part in (settings.notifications_stub_fail or "").split(",")
    )
    return StubSender(fail_types=fail)


__all__ = [
    "Outcome",
    "Sender",
    "StubSender",
    "StubSenderRefused",
    "TelegramSender",
    "build_stub_sender",
]
