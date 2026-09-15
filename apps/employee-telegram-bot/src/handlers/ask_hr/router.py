"""Вопрос в отдел кадров из чата.

Два входа:

  * кнопка «Написать в HR» (или /ask) — бот ждёт одно сообщение и
    передаёт его в CRM;
  * ответ на сообщение HR — то, что человек и так сделает, когда HR
    попросит уточнение. Узнаётся по заголовку, с которого backend
    начинает каждый ответ HR.

В какое обращение ляжет сообщение, решает backend: бот не знает ни
номеров обращений, ни их состояний и не должен знать.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.handlers.menu.router import _guard, build_menu
from src.keyboards import employee as kb
from src.messages import employee as text

logger = logging.getLogger(__name__)

router = Router(name="ask-hr")

#: Первая строка ответа HR. Держится вровень с `REPLY_HEADER` в backend.
REPLY_PREFIX = "💬 Ответ HR по обращению №"


class AskHr(StatesGroup):
    waiting_text = State()


@router.message(F.text == kb.BTN_ASK_HR)
@router.message(Command("ask"))
async def ask_start(message: Message, state: FSMContext, employee, denial) -> None:
    if not await _guard(message, employee, denial):
        return
    await state.set_state(AskHr.waiting_text)
    await message.answer(text.ASK_HR_PROMPT, reply_markup=kb.cancel_menu())


@router.message(StateFilter(AskHr.waiting_text), F.text == kb.BTN_CANCEL)
@router.message(StateFilter(AskHr.waiting_text), Command("cancel"))
async def ask_cancel(message: Message, state: FSMContext, employee, denial) -> None:
    await state.clear()
    await message.answer(
        text.ASK_HR_CANCELLED, reply_markup=build_menu(employee, message)
    )


@router.message(StateFilter(AskHr.waiting_text))
async def ask_send(
    message: Message, state: FSMContext, employee, denial, client: SelfServiceClient
) -> None:
    if not (message.text or "").strip():
        # Состояние не снимается: человек прислал фото вместо текста и
        # всё ещё хочет задать вопрос.
        await message.answer(text.ASK_HR_EMPTY)
        return
    await state.clear()
    await forward(message, employee, denial, client)


@router.message(
    StateFilter(None),
    F.text,
    F.reply_to_message.text.startswith(REPLY_PREFIX),
)
async def reply_to_hr(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    await forward(message, employee, denial, client)


async def forward(message: Message, employee, denial, client: SelfServiceClient) -> None:
    if not await _guard(message, employee, denial):
        return
    try:
        result = await client.ask_hr(
            message.from_user.id,
            text=message.text.strip(),
            message_id=message.message_id,
        )
    except ApiError as error:
        # Текста вопроса в журнале нет: это личная переписка человека с HR.
        logger.info(
            "question to HR refused for %s: %s", message.from_user.id, error.code
        )
        await message.answer(
            text.ASK_HR_FAILED, reply_markup=build_menu(employee, message)
        )
        return
    await message.answer(
        text.ask_hr_sent(result), reply_markup=build_menu(employee, message)
    )


__all__ = ["AskHr", "REPLY_PREFIX", "ask_cancel", "ask_send", "ask_start",
           "forward", "reply_to_hr", "router"]
