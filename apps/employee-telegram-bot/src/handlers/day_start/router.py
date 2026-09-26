"""Ответ на напоминание о начале дня.

Три кнопки под напоминанием: отметиться, «Опаздываю», «Не приду».
Первая открывает Mini App и сюда не приходит вовсе. Две другие — это
callback, и разбираются здесь.

**Причина спрашивается, но не требуется.** У «Опаздываю» её можно
пропустить одной кнопкой: человек, стоящий в пробке, пишет «пробки» не
потому, что это кому-то нужно, а потому, что иначе не отправится. У
«Не приду» причина спрашивается настойчивее — HR всё равно спросит её
сам, — но и здесь отказ не блокирует ответ: предупреждение без причины
лучше, чем молчание.

**Ни отпуска, ни больничного это не оформляет.** Сказанное в чате
объясняет пустую строку в табеле и ничего больше; заявка проходит
согласование и подаётся в Mini App. Бот говорит об этом прямо, иначе
человек решит, что больничный уже открыт, и не подаст заявку.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.messages import day_start as text
from src.notifications.buttons import MARK_NOW, SAY_ABSENT, SAY_LATE

logger = logging.getLogger(__name__)

router = Router(name="day-start")


class DayStart(StatesGroup):
    """Ждём причину. Вид ответа лежит в данных состояния."""

    waiting_reason = State()


@router.callback_query(F.data == MARK_NOW)
async def mark_now(call: CallbackQuery) -> None:
    """Запасной ответ, когда Mini App не настроен.

    Кнопка в этом случае не открывает приложение, и молчать на неё
    нельзя: человек будет нажимать ещё, решив, что не попал.
    """
    await call.answer()
    if call.message is not None:
        await call.message.answer(text.MARK_HINT)


@router.callback_query(F.data.in_({SAY_LATE, SAY_ABSENT}))
async def ask_reason(call: CallbackQuery, state: FSMContext) -> None:
    kind = "LATE" if call.data == SAY_LATE else "ABSENT"
    await state.set_state(DayStart.waiting_reason)
    await state.update_data(day_notice_kind=kind)

    await call.answer()
    if call.message is not None:
        await call.message.answer(
            text.ASK_LATE_REASON if kind == "LATE" else text.ASK_ABSENT_REASON,
            reply_markup=text.skip_markup(),
        )


@router.callback_query(StateFilter(DayStart.waiting_reason), F.data == text.SKIP)
async def skip_reason(
    call: CallbackQuery, state: FSMContext, client: SelfServiceClient
) -> None:
    """Отправить без причины. Предупреждение важнее объяснения."""
    await call.answer()
    await _send(call.message, call.from_user.id, state, client, comment=None)


@router.message(StateFilter(DayStart.waiting_reason))
async def reason_typed(
    message: Message, state: FSMContext, client: SelfServiceClient
) -> None:
    written = (message.text or "").strip()
    if not written:
        # Состояние не снимается: человек прислал фото вместо текста и
        # всё ещё хочет предупредить.
        await message.answer(text.REASON_EMPTY)
        return
    if message.from_user is None:
        return
    await _send(message, message.from_user.id, state, client, comment=written)


async def _send(
    target: Message | None,
    telegram_user_id: int,
    state: FSMContext,
    client: SelfServiceClient,
    *,
    comment: str | None,
) -> None:
    data = await state.get_data()
    kind = str(data.get("day_notice_kind") or "LATE")
    await state.clear()
    if target is None:
        return

    try:
        await client.day_notice(
            telegram_user_id=telegram_user_id, kind=kind, comment=comment
        )
    except ApiError:
        logger.exception("day notice failed")
        await target.answer(text.FAILED)
        return

    await target.answer(text.LATE_SAVED if kind == "LATE" else text.ABSENT_SAVED)


__all__ = ["DayStart", "router"]
