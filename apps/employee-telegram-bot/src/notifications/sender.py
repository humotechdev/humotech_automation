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
from aiogram.types import BufferedInputFile

from src.api.errors import ApiError, NotFound
from src.config.settings import settings
from src.notifications.buttons import markup_for
from src.utils.safe import is_uuid

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
        self, *, chat_id: int, text: str, notification_type: str,
        entity_id: str | None = None,
        attachment: str | None = None,
        telegram_user_id: int | None = None,
    ) -> Outcome: ...


#: Предел подписи к файлу в Telegram — короче предела сообщения (4096).
CAPTION_LIMIT = 1024

#: Уведомления очереди уходят БЕЗ разметки. Бот по умолчанию шлёт с
#: `parse_mode=HTML`, а тексты очереди — обычный текст, собранный backend
#: из имён, названий, причин отказа и ответов кадровика. С разметкой
#: `<a href="…">` в ответе HR стал бы ссылкой на подменённый адрес, а «<»
#: в причине отказа сорвал бы доставку («can't parse entities») — и
#: очередь повторяла бы её до исчерпания попыток. Явный `None` в aiogram
#: отключает умолчание бота.
PLAIN = None


class TelegramSender:
    """Настоящая доставка. Единственное место, где бот пишет в чат.

    `client` нужен ради вложений: заявление на больничный приходит
    человеку файлом, а файл собирается из заявки на каждое обращение и
    в очереди не лежит. Без клиента отправщик остаётся прежним и шлёт
    только текст.
    """

    def __init__(self, bot: Bot, client=None) -> None:
        self._bot = bot
        self._client = client

    async def deliver(
        self, *, chat_id: int, text: str, notification_type: str,
        entity_id: str | None = None,
        attachment: str | None = None,
        telegram_user_id: int | None = None,
    ) -> Outcome:
        # Тип теперь важен: по нему под сообщением появляется кнопка.
        # Какая именно — решает `buttons.py`, а не это место.
        markup = markup_for(notification_type, entity_id)
        try:
            document = await self._fetch(attachment, entity_id, telegram_user_id)
            if isinstance(document, _Unavailable):
                if attachment == "question_file":
                    # Окончательный отказ: backend не отдаёт файл (не прошёл
                    # проверку, удалён — 404) или ссылка на него негодная.
                    # Повтор ничего не изменит; очередь по этому коду сразу
                    # переводит строку в FAILED (`PERMANENT_ERRORS`).
                    return Outcome(sent=False, error=document.code)
                # К заявке бумага — приложение: сообщение уходит без неё.
                document = None
            if document is None and attachment == "question_file":
                # Файл и есть ответ кадровика. Текст без него — «вот
                # бланк» без бланка; пусть очередь повторит позже.
                return Outcome(sent=False, error="attachment_unavailable")
            if document is not None:
                content, name = document
                if len(text) > CAPTION_LIMIT:
                    # Подпись к файлу в Telegram короче сообщения: длинный
                    # ответ уходит текстом, а файл — следом, с первой
                    # строкой ответа. По ней бот узнаёт ответ HR, когда
                    # человек отвечает прямо на файл.
                    await self._bot.send_message(
                        chat_id, text, reply_markup=markup, parse_mode=PLAIN,
                    )
                    await self._bot.send_document(
                        chat_id,
                        BufferedInputFile(content, filename=name),
                        caption=text.split("\n", 1)[0],
                        parse_mode=PLAIN,
                    )
                    return Outcome(sent=True)
                # Текст уходит подписью к файлу, а не отдельным
                # сообщением: два сообщения подряд про одно и то же
                # человек читает как два разных события.
                await self._bot.send_document(
                    chat_id,
                    BufferedInputFile(content, filename=name),
                    caption=text,
                    reply_markup=markup,
                    parse_mode=PLAIN,
                )
                return Outcome(sent=True)
            await self._bot.send_message(
                chat_id, text, reply_markup=markup, parse_mode=PLAIN,
            )
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

    async def _fetch(
        self, attachment: str | None, entity_id: str | None,
        telegram_user_id: int | None,
    ) -> tuple[bytes, str] | _Unavailable | None:
        """Вложение, если оно полагается и его удалось получить.

        Неудача здесь НЕ проваливает доставку: человеку важнее узнать,
        что заявка создана, чем не узнать ничего из-за недоступной
        бумаги. Заявление он в любом случае откроет из кабинета.

        `_Unavailable` — отказ окончательный (404, негодный идентификатор),
        `None` — временный (сеть, 5xx) или вложение не полагается.
        """
        if attachment not in (
            "absence_application", "absence_certificate", "question_file",
        ):
            return None
        if self._client is None or entity_id is None or telegram_user_id is None:
            return None
        if not is_uuid(entity_id):
            # Идентификатор идёт в путь запроса с общим секретом бота.
            # Не UUID — значит, не наш: `../` в нём увёл бы запрос на
            # другой адрес backend.
            logger.warning("attachment skipped: entity id is not a UUID")
            return _Unavailable("attachment_rejected")
        try:
            if attachment == "question_file":
                return await self._client.question_reply_file(
                    telegram_user_id, entity_id
                )
            if attachment == "absence_certificate":
                return await self._client.absence_certificate(
                    telegram_user_id, entity_id
                )
            return await self._client.absence_application(
                telegram_user_id, entity_id
            )
        except NotFound:
            # Файла нет или он не прошёл проверку: backend отдаёт 404, и
            # от повторного вопроса он не появится.
            logger.info("paper not attached: not found")
            return _Unavailable("attachment_not_found")
        except ApiError as error:
            logger.info("paper not attached: %s", error.code)
            return None


class _Unavailable:
    """Вложение окончательно недоступно. `code` уходит в отчёт очереди."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code


class StubSender:
    """Никуда не ходит. Пишет строку в журнал и отвечает очереди.

    Нужна ровно одна возможность сверх «всегда успех»: заранее названные
    типы уведомлений объявляются неудачными. Без неё нельзя проверить ни
    повтор, ни исчерпание попыток, а именно там очередь и ошибается.
    """

    def __init__(self, fail_types: tuple[str, ...] = ()) -> None:
        self._fail = tuple(part for part in fail_types if part)

    async def deliver(
        self, *, chat_id: int, text: str, notification_type: str,
        entity_id: str | None = None,
        attachment: str | None = None,
        telegram_user_id: int | None = None,
    ) -> Outcome:
        del text  # содержимое проверочного сообщения в журнал не идёт
        del entity_id  # заглушка кнопок не рисует
        del attachment, telegram_user_id  # и файлов никуда не носит
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
