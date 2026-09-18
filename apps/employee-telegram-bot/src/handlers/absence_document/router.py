"""Загрузка справки к заявке прямо из чата.

Человек читает «справка не принята, загрузите корректный документ» —
и нажимает кнопку под этим же сообщением. Заявку, к которой прикладывать,
кнопка знает сама: у человека их бывает несколько, и заставлять его
выбирать из списка после отказа — лишний шаг там, где всё уже известно.

**Файл проверяет сервер.** Бот смотрит только на размер, и то лишь
чтобы не начинать качать заведомо лишнее: тип, вес и безопасность
решает то же место, что и для Mini App. Вторая проверка в боте — это
второе место, где однажды забудут обновить список разрешённых типов.

**Сжатое фото — это фото.** Telegram присылает снимок как `photo` без
имени файла; имя придумывается здесь, потому что серверу оно нужно, а
человеку всё равно.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.messages import absence_document as text
from src.notifications.buttons import UPLOAD_DOCUMENT

logger = logging.getLogger(__name__)

router = Router(name="absence-document")


class Upload(StatesGroup):
    """Ждём файл. Заявка лежит в данных состояния."""

    waiting_file = State()


@router.callback_query(F.data.startswith(UPLOAD_DOCUMENT))
async def ask_file(call: CallbackQuery, state: FSMContext) -> None:
    request_id = (call.data or "")[len(UPLOAD_DOCUMENT):]
    await call.answer()
    if not request_id or call.message is None:
        return

    await state.set_state(Upload.waiting_file)
    await state.update_data(absence_request_id=request_id)
    await call.message.answer(text.ASK_FILE)


@router.message(StateFilter(Upload.waiting_file), Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(text.CANCELLED)


@router.message(StateFilter(Upload.waiting_file), F.document | F.photo)
async def receive(
    message: Message, state: FSMContext, client: SelfServiceClient
) -> None:
    data = await state.get_data()
    request_id = str(data.get("absence_request_id") or "")
    if not request_id or message.from_user is None:
        await state.clear()
        return

    kept = _file_of(message)
    if kept is None:
        await message.answer(text.NOT_A_FILE)
        return
    if (kept.size or 0) > text.MAX_BYTES:
        # Состояние не снимается: человек пришлёт снимок поменьше.
        await message.answer(text.TOO_BIG)
        return

    bot = message.bot
    if bot is None:
        return

    try:
        stream = await bot.download(kept.file_id)
        content = stream.read() if stream is not None else b""
    except Exception:
        logger.exception("не удалось скачать файл справки")
        await message.answer(text.FAILED)
        return

    await state.clear()
    try:
        await client.upload_absence_document(
            telegram_user_id=message.from_user.id,
            request_id=request_id,
            filename=kept.name,
            content=content,
            content_type=kept.mime_type,
        )
    except ApiError as failed:
        logger.info("справка не принята сервером: %s", failed.status)
        # Отказ сервера показывается его словами, если они есть: он
        # объясняет, что именно не так — тип, размер или закрытая заявка.
        await message.answer(failed.message or text.FAILED)
        return
    except Exception:
        logger.exception("справка не ушла на сервер")
        await message.answer(text.FAILED)
        return

    await message.answer(text.SAVED)


class _Kept:
    """Файл, приведённый к одному виду: документ и фото различаются."""

    __slots__ = ("file_id", "name", "mime_type", "size")

    def __init__(self, file_id: str, name: str, mime_type: str, size: int | None):
        self.file_id = file_id
        self.name = name
        self.mime_type = mime_type
        self.size = size


def _file_of(message: Message) -> _Kept | None:
    if message.document is not None:
        return _Kept(
            file_id=message.document.file_id,
            name=message.document.file_name or "spravka",
            mime_type=message.document.mime_type or "application/octet-stream",
            size=message.document.file_size,
        )
    if message.photo:
        # Последний размер — самый большой: Telegram отдаёт их по
        # возрастанию, и брать первый значит прислать превью.
        largest = message.photo[-1]
        return _Kept(
            file_id=largest.file_id,
            name="spravka.jpg",
            mime_type="image/jpeg",
            size=largest.file_size,
        )
    return None


__all__ = ["Upload", "router"]
