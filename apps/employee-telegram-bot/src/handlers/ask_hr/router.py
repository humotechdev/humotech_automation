"""Сообщение в отдел кадров из чата.

Один сценарий на два входа — нижнюю кнопку «Написать HR» (или /ask) и
кнопку «Написать HR» под ответом ассистента, у которого ответа не нашлось:

    приглашение → человек пишет текст → текст сразу уходит HR →
    «Сообщение отправлено HR.»

Подтверждений и предпросмотров нет: человек нажал «Написать HR» и
написал — это и есть решение отправить. Кнопка под ответом ассистента
исходный вопрос НЕ отправляет: HR получает то, что человек написал после
приглашения, и второго обращения из одного вопроса не возникает.

Ещё два входа к тому же обращению:

  * ответ (reply в Telegram) на сообщение HR — продолжение разговора;
    узнаётся по заголовку, с которого backend начинает сообщения HR;
  * просто текст в чате — вопрос ассистенту; HR он не уходит.

В какое обращение ляжет сообщение, решает backend: бот не знает ни
номеров обращений, ни их состояний и не должен знать.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.api.errors import ApiError, BadRequest, Forbidden, Unauthorized
from src.api.selfservice import SelfServiceClient
from src.handlers.menu.router import _guard, build_menu
from src.keyboards import employee as kb
from src.messages import employee as text

logger = logging.getLogger(__name__)

router = Router(name="ask-hr")
#: Стоит ДО меню (см. `handlers/__init__.py`): выводит из ввода сообщения,
#: если человек нажал кнопку меню или набрал команду.
guard_router = Router(name="ask-hr-guard")

#: «Написать HR» под ответом ассистента. Следом — номер сообщения с
#: вопросом; старые кнопки «Передать HR» в истории чатов несут тот же код
#: и теперь тоже открывают приглашение.
ESCALATE = "hr:"
#: «Повторить отправку». Следом — номер сообщения Telegram.
RETRY = "hrsend:"

#: Первые строки сообщений HR. Держатся вровень с `ANSWER_HEADER` и
#: `MESSAGE_HEADER` в backend (`questions/inbox.py`).
HR_HEADERS = ("HR ответил на ваш вопрос", "Сообщение от HR")
#: Заголовок прежней версии: такие сообщения ещё лежат в чатах, и ответ
#: на них — тот же ответ HR.
LEGACY_HR_HEADER = "💬 Ответ HR по обращению №"

ASK_BUTTONS = (kb.BTN_ASK_HR, kb.BTN_ASK_HR_LEGACY)

#: Где в данных состояния лежит неотправленное сообщение для повтора.
PENDING = "hr_pending"

#: Сбой, после которого неизвестно, дошло ли сообщение: сервер мог его
#: сохранить, а ответ потеряться.
UNSURE = (aiohttp.ClientError, asyncio.TimeoutError)


class AskHr(StatesGroup):
    waiting_text = State()


# --- выход из ввода ------------------------------------------------------------


def _leaves_compose(value: str | None) -> bool:
    """Кнопка меню или команда — не текст для HR.

    «Отмена» и сама «Написать HR» обрабатываются в сценарии: первая
    отменяет, вторая повторяет приглашение.
    """
    if not value:
        return False
    if value in ASK_BUTTONS or value == kb.BTN_CANCEL:
        return False
    if value.startswith("/"):
        command = value.split()[0].split("@")[0]
        return command not in ("/cancel", "/ask")
    return value in kb.ALL_BUTTONS


@guard_router.message(StateFilter(AskHr.waiting_text), F.text.func(_leaves_compose))
async def leave_compose(message: Message, state: FSMContext) -> None:
    # Снять ожидание и отдать сообщение дальше: кнопка меню остаётся
    # кнопкой меню, а следующий текст уже не уйдёт HR случайно.
    await state.clear()
    raise SkipHandler()


# --- приглашение ---------------------------------------------------------------


@router.message(F.text.in_(ASK_BUTTONS))
@router.message(Command("ask"))
async def ask_start(message: Message, state: FSMContext, employee, denial) -> None:
    # Повторное нажатие лишь повторяет приглашение: состояние одно, и
    # обращение само по себе не создаётся.
    if not await _guard(message, employee, denial):
        return
    await _invite(message, state)


@router.callback_query(F.data.startswith(ESCALATE))
async def escalate(call: CallbackQuery, state: FSMContext, employee, denial) -> None:
    """«Написать HR» под ответом ассистента — то же приглашение."""
    await call.answer()
    if not isinstance(call.message, Message):
        return
    if not await _guard(call.message, employee, denial):
        return
    await _invite(call.message, state)


async def _invite(message: Message, state: FSMContext) -> None:
    await state.set_state(AskHr.waiting_text)
    await message.answer(text.ASK_HR_PROMPT, reply_markup=kb.cancel_menu())


@router.message(StateFilter(AskHr.waiting_text), F.text == kb.BTN_CANCEL)
@router.message(StateFilter(AskHr.waiting_text), Command("cancel"))
async def ask_cancel(message: Message, state: FSMContext, employee, denial) -> None:
    await state.clear()
    await message.answer(
        text.ASK_HR_CANCELLED, reply_markup=build_menu(employee, message)
    )


# --- текст для HR ----------------------------------------------------------------


@router.message(StateFilter(AskHr.waiting_text))
async def ask_send(
    message: Message, state: FSMContext, employee, denial, client: SelfServiceClient
) -> None:
    """Текст после приглашения уходит HR сразу, без подтверждения."""
    if message.text is None:
        # Фото, файл, стикер. Ждём дальше: человек всё ещё пишет HR.
        await message.answer(text.ASK_HR_NOT_TEXT)
        return
    body = message.text.strip()
    if not body:
        await message.answer(text.ASK_HR_EMPTY)
        return
    if len(body) > text.ASK_HR_MAX_LENGTH:
        await message.answer(text.ASK_HR_TOO_LONG)
        return
    # Ожидание снимается ДО отправки: следующее сообщение, пришедшее,
    # пока идёт запрос, уже не уйдёт HR вторым.
    await state.clear()
    if not await _guard(message, employee, denial):
        return
    await _deliver(
        message, state, employee, client,
        user_id=message.from_user.id, message_id=message.message_id,
        body=body, starts_question=True,
    )


def _is_reply_to_hr(message: Message) -> bool:
    original = message.reply_to_message
    if original is None or original.from_user is None or not original.from_user.is_bot:
        return False
    head = original.text or original.caption or ""
    return head.startswith(HR_HEADERS) or head.startswith(LEGACY_HR_HEADER)


@router.message(StateFilter(None), F.text, F.func(_is_reply_to_hr))
async def reply_to_hr(
    message: Message, state: FSMContext, employee, denial, client: SelfServiceClient
) -> None:
    """Ответ человека на сообщение HR — продолжение того же разговора.

    Ассистент здесь не спрашивается: разговор с кадровиком уже идёт, и
    вклиниваться в него ответом из правил — значит перебивать. Новым
    вопросом это не считается.
    """
    if not await _guard(message, employee, denial):
        return
    body = message.text.strip()
    if not body:
        await message.answer(text.ASK_HR_EMPTY)
        return
    if len(body) > text.ASK_HR_MAX_LENGTH:
        await message.answer(text.ASK_HR_TOO_LONG)
        return
    await _deliver(
        message, state, employee, client,
        user_id=message.from_user.id, message_id=message.message_id,
        body=body, starts_question=False,
    )


async def _deliver(
    message: Message, state: FSMContext, employee, client: SelfServiceClient,
    *, user_id: int, message_id: int, body: str, starts_question: bool,
) -> None:
    """Отправить и честно сказать, что вышло.

    Успех — только когда backend ответил. Неизвестность (сеть, таймаут,
    5xx) — не успех и не провал: сообщение могло лечь. Повтор шлёт ТО
    ЖЕ сообщение с тем же номером Telegram, и backend второй раз его не
    кладёт.
    """
    try:
        await client.escalate(
            user_id, text=body, message_id=message_id,
            starts_question=starts_question,
        )
    except BadRequest:
        # Сервер отказал в самом тексте. Повтор того же ничего не даст —
        # ждём новый текст в том же сценарии.
        await state.set_state(AskHr.waiting_text)
        await message.answer(text.ASK_HR_REJECTED, reply_markup=kb.cancel_menu())
        return
    except (Forbidden, Unauthorized) as error:
        logger.info("message to HR refused for %s: %s", user_id, error.code)
        await message.answer(text.NO_ACCESS)
        return
    except (ApiError, *UNSURE) as error:
        # Текста сообщения в журнале нет: это личная переписка с HR.
        logger.info(
            "message to HR not confirmed for %s: %s",
            user_id, getattr(error, "code", type(error).__name__),
        )
        await state.update_data({PENDING: {
            "id": message_id, "text": body, "starts_question": starts_question,
        }})
        await message.answer(text.ASK_HR_FAILED, reply_markup=retry_markup(message_id))
        return
    await message.answer(text.ASK_HR_SENT, reply_markup=build_menu(employee, message))


@router.callback_query(F.data.startswith(RETRY))
async def retry(
    call: CallbackQuery, state: FSMContext, employee, client: SelfServiceClient
) -> None:
    """«Повторить отправку»: то же сообщение, тот же номер — без дубля."""
    await call.answer()
    if not isinstance(call.message, Message) or call.from_user is None:
        return
    pending = (await state.get_data()).get(PENDING)
    key = (call.data or "")[len(RETRY):]
    if not pending or str(pending.get("id")) != key:
        # Уже ушло (кнопку нажали второй раз) или бот перезапустился и
        # текста не помнит. Молчать нельзя: человек нажал и ждёт.
        await call.message.answer(text.ASK_HR_RETRY_GONE)
        return
    await state.update_data({PENDING: None})
    await _deliver(
        call.message, state, employee, client,
        user_id=call.from_user.id, message_id=int(pending["id"]),
        body=pending["text"], starts_question=bool(pending.get("starts_question")),
    )


# --- вопрос ассистенту ---------------------------------------------------------------


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def ask_assistant(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    await forward(message, employee, denial, client)


async def forward(message: Message, employee, denial, client: SelfServiceClient) -> None:
    """Вопрос в чате: сперва ассистент, HR — только по кнопке.

    Ответ, который есть в правилах компании, человек получает сразу и не
    ждёт кадровика. Если ответа нет — кнопка «Написать HR», которая ведёт
    в общий сценарий. Сам вопрос HR при этом не уходит.
    """
    if not await _guard(message, employee, denial):
        return

    asked = message.text.strip()
    try:
        answer = await client.ask(message.from_user.id, text=asked)
    except (ApiError, *UNSURE) as error:
        # Текста вопроса в журнале нет: это личная переписка человека с HR.
        logger.info(
            "assistant refused for %s: %s",
            message.from_user.id, getattr(error, "code", type(error).__name__),
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

    # Ответа нет — и придумывать его нельзя. Решение писать HR — за
    # человеком.
    await message.answer(
        text.ASK_NO_ANSWER,
        reply_markup=escalate_markup(message.message_id),
    )


def escalate_markup(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=text.ASK_ESCALATE_BUTTON,
                callback_data=f"{ESCALATE}{message_id}",
            )
        ]]
    )


def retry_markup(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=text.ASK_HR_RETRY_BUTTON,
                callback_data=f"{RETRY}{message_id}",
            )
        ]]
    )


__all__ = ["ASK_BUTTONS", "AskHr", "ESCALATE", "HR_HEADERS", "LEGACY_HR_HEADER",
           "RETRY", "ask_assistant", "ask_cancel", "ask_send", "ask_start",
           "escalate", "escalate_markup", "forward", "guard_router",
           "leave_compose", "reply_to_hr", "retry", "retry_markup", "router"]
