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
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.handlers.menu.router import _guard, build_menu
from src.keyboards import employee as kb
from src.messages import employee as text

logger = logging.getLogger(__name__)

router = Router(name="ask-hr")

#: Код нажатия «Передать HR». Следом идёт номер сообщения с вопросом.
ESCALATE = "hr:"

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
    await reply_to_hr_forward(message, employee, denial, client)


async def forward(message: Message, employee, denial, client: SelfServiceClient) -> None:
    """Новый вопрос: сперва ассистент, HR — только по кнопке.

    Ответ, который есть в правилах компании, человек получает сразу и не
    ждёт кадровика. Кадровик, в свою очередь, не получает очередь из
    вопросов, на которые правила уже отвечают.
    """
    if not await _guard(message, employee, denial):
        return

    asked = message.text.strip()
    try:
        answer = await client.ask(message.from_user.id, text=asked)
    except ApiError as error:
        # Текста вопроса в журнале нет: это личная переписка человека с HR.
        logger.info(
            "assistant refused for %s: %s", message.from_user.id, error.code
        )
        answer = None

    if answer and answer.get("answered") and answer.get("answer"):
        await message.answer(
            text.ask_answered(
                str(answer["answer"]), list(answer.get("sources") or [])
            ),
            reply_markup=build_menu(employee, message),
        )
        return

    # Ответа нет — и придумывать его нельзя. Вопрос остаётся у человека,
    # пока он сам не решит передать его кадровику.
    await message.answer(
        text.ASK_NO_ANSWER,
        reply_markup=escalate_markup(message.message_id),
    )
    _remember(message.from_user.id, message.message_id, asked)


async def reply_to_hr_forward(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    """Ответ человека в уже открытом обращении.

    Ассистент здесь не спрашивается вовсе: разговор с кадровиком уже
    идёт, и вклиниваться в него ответом из правил — значит перебивать.
    """
    if not await _guard(message, employee, denial):
        return
    try:
        result = await client.escalate(
            message.from_user.id,
            text=message.text.strip(),
            message_id=message.message_id,
        )
    except ApiError as error:
        logger.info(
            "reply to HR refused for %s: %s", message.from_user.id, error.code
        )
        await message.answer(
            text.ASK_HR_FAILED, reply_markup=build_menu(employee, message)
        )
        return
    await message.answer(
        text.ask_hr_sent(result), reply_markup=build_menu(employee, message)
    )


@router.callback_query(F.data.startswith(ESCALATE))
async def escalate(call: CallbackQuery, client: SelfServiceClient) -> None:
    """«Передать HR». Уходит ИСХОДНЫЙ вопрос, а не ответ ассистента."""
    await call.answer()
    if call.message is None or call.from_user is None:
        return

    key = (call.data or "")[len(ESCALATE):]
    asked = _recall(call.from_user.id, key)
    if not asked:
        # Бот перезапустили, и вопрос забылся. Молчать нельзя: человек
        # нажал кнопку и ждёт.
        await call.message.answer(text.ASK_ESCALATE_LOST)
        return

    try:
        await client.escalate(
            call.from_user.id, text=asked, message_id=int(key) if key.isdigit() else None
        )
    except ApiError as error:
        logger.info("escalation refused for %s: %s", call.from_user.id, error.code)
        await call.message.answer(text.ASK_ESCALATE_FAILED)
        return

    _forget(call.from_user.id, key)
    await call.message.answer(text.ASK_ESCALATED)


def escalate_markup(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=text.ASK_ESCALATE_BUTTON,
                callback_data=f"{ESCALATE}{message_id}",
            )
        ]]
    )


#: Вопросы, ждущие решения «передать или нет».
#:
#: В памяти процесса, а не в базе: это черновик длиной в одно нажатие.
#: Переживать перезапуск ему незачем — человек просто спросит заново, и
#: бот прямо об этом скажет. Хранить чужие вопросы дольше нужного —
#: лишний риск без пользы.
_PENDING: dict[tuple[int, str], str] = {}

#: Сколько вопросов держим. Потолок, чтобы память не росла бесконечно
#: на чате, где кнопку не нажимают никогда.
_PENDING_LIMIT = 500


def _remember(user_id: int, message_id: int, asked: str) -> None:
    if len(_PENDING) >= _PENDING_LIMIT:
        # Выбрасываем самый старый: словарь в Python сохраняет порядок
        # вставки, и первый ключ — самый давний.
        _PENDING.pop(next(iter(_PENDING)), None)
    _PENDING[(user_id, str(message_id))] = asked


def _recall(user_id: int, key: str) -> str | None:
    return _PENDING.get((user_id, key))


def _forget(user_id: int, key: str) -> None:
    _PENDING.pop((user_id, key), None)


__all__ = ["AskHr", "ESCALATE", "REPLY_PREFIX", "ask_cancel", "ask_send",
           "ask_start", "escalate", "escalate_markup", "forward",
           "reply_to_hr", "reply_to_hr_forward", "router"]
