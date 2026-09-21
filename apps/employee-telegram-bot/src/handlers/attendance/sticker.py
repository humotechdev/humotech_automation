"""Отметка по печатному QR-коду офиса.

Наклейка у двери несёт ссылку `https://t.me/<бот>?start=qr_<секрет>`.
Камера телефона открывает по ней Telegram, и бот получает `/start` с
этой нагрузкой. Геопозиции в `/start` нет и быть не может — Telegram её
туда не кладёт, — поэтому бот просит её отдельным сообщением кнопкой
`request_location`, а уже потом идёт в backend.

Что решает бот: только порядок разговора. Разрешать ли отметку — офис,
направление, радиус, включена ли точка, не перевыпущен ли код — решает
backend тем же `scanning.scan()`, что и для кода на экране.

Что бот отсекает сам, до backend:

  * пересланную геопозицию — она чужая или старая, а нужна текущая;
  * геопозицию, пришедшую слишком поздно после скана, — код могли
    отсканировать у двери, а отправить место уже из дома.

Ни секрета из ссылки, ни координат в журнал не пишется: по первому
собирается рабочая наклейка, по вторым — маршрут человека.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.handlers.attendance.router import answer
from src.keyboards import employee as kb
from src.messages import attendance as text

logger = logging.getLogger(__name__)

router = Router(name="sticker")

#: Префикс нагрузки `/start` печатного кода. Тот же, что у backend.
PREFIX = "qr_"
MAX_PAYLOAD = 64

#: Сколько ждать геопозицию после скана.
WAIT_SECONDS = 180
#: Насколько старой может быть сама геопозиция по времени сообщения.
LOCATION_MAX_AGE_SECONDS = 60


class StickerScan(StatesGroup):
    waiting_location = State()


def sticker_payload(args: str | None) -> str | None:
    """Нагрузка печатного кода из `/start`, либо `None`.

    Разбор только по виду: настоящий ли это секрет, знает backend.
    """
    if not args:
        return None
    value = args.strip()
    if not value.startswith(PREFIX) or len(value) <= len(PREFIX):
        return None
    if len(value) > MAX_PAYLOAD:
        return None
    return value


async def begin(message: Message, payload: str, state: FSMContext, employee, denial) -> None:
    """Скан печатного кода: запомнить нагрузку и попросить геопозицию."""
    if employee is None:
        # Непривязанному отмечаться нечем: объяснение то же, что на /start.
        from src.handlers.menu.router import start as plain_start

        await plain_start(message, employee, denial)
        return

    await state.set_state(StickerScan.waiting_location)
    await state.update_data(sticker=payload, asked_at=time.time())
    await message.answer(text.ASK_LOCATION, reply_markup=kb.location_request())


@router.message(StickerScan.waiting_location, F.text == kb.BTN_CANCEL)
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await answer(message, text.LOCATION_CANCELLED)


@router.message(
    StickerScan.waiting_location, F.location, F.chat.type == ChatType.PRIVATE
)
async def location(message: Message, state: FSMContext, client: SelfServiceClient) -> None:
    """Геопозиция после скана — одна попытка отметки."""
    user = message.from_user
    if user is None:
        return

    data = await state.get_data()

    # Пересланная геопозиция — чужая или старая. Ждём дальше: человек
    # нажмёт кнопку и пришлёт свою.
    if getattr(message, "forward_origin", None) is not None:
        await message.answer(text.LOCATION_FORWARDED, reply_markup=kb.location_request())
        return

    await state.clear()
    payload = data.get("sticker")
    asked_at = float(data.get("asked_at") or 0)
    if not payload or time.time() - asked_at > WAIT_SECONDS or _stale(message):
        await answer(message, text.LOCATION_EXPIRED)
        return

    place = message.location
    try:
        result = await client.scan(
            telegram_user_id=user.id,
            token=payload,
            # Ключ попытки — из самого сообщения: повторная доставка того
            # же сообщения даёт тот же ключ, и backend ответит тем же.
            client_event_id=f"tg-{message.chat.id}-{message.message_id}",
            latitude=place.latitude,
            longitude=place.longitude,
            accuracy_m=place.horizontal_accuracy,
        )
    except ApiError as error:
        reason = (error.details or {}).get("reason") if error.details else None
        logger.info("sticker scan refused for %s: %s", user.id, reason or error.code)
        await answer(message, text.ACCESS_MESSAGES.get(reason, text.SCAN_FAILED))
        return
    except Exception:
        logger.exception("sticker scan failed")
        await answer(message, text.SCAN_FAILED)
        return

    logger.info("sticker scan handled for %s: %s", user.id, result.get("status"))
    await answer(message, text.outcome(result))


def _stale(message: Message) -> bool:
    sent = getattr(message, "date", None)
    if not isinstance(sent, datetime):
        return False
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    return (datetime.now(tz=timezone.utc) - sent).total_seconds() > LOCATION_MAX_AGE_SECONDS


__all__ = ["PREFIX", "StickerScan", "begin", "router", "sticker_payload"]
