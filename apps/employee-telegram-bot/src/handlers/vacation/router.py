"""Заявка на отпуск — эталонный FSM-сценарий.

Остальные многошаговые сценарии (больничный, корректировка отметки) делаются
по этому же образцу: StatesGroup -> шаги -> подтверждение -> один вызов API.
Обратите внимание: количество дней и остаток отпуска бот НЕ считает —
их возвращает бэкенд (правило №3).
"""

from __future__ import annotations

from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.api import BackendClient
from src.api.errors import ApiError
from src.keyboards import menu as kb
from src.messages import ru
from src.states.flows import VacationRequest
from src.utils.filters import IsLinked

router = Router(name="vacation")
router.message.filter(IsLinked())
router.callback_query.filter(IsLinked())

DATE_FMT = "%d.%m.%Y"


def _parse_date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw.strip(), DATE_FMT).date()
    except ValueError:
        return None


@router.message(Command("vacation"))
@router.message(F.text == kb.BTN_VACATION)
async def start_vacation(message: Message, state: FSMContext) -> None:
    await state.set_state(VacationRequest.date_from)
    await message.answer(ru.VACATION_ASK_FROM, reply_markup=kb.cancel_menu())


@router.message(VacationRequest.date_from, F.text)
async def set_date_from(message: Message, state: FSMContext) -> None:
    parsed = _parse_date(message.text)
    if parsed is None:
        await message.answer(ru.DATE_INVALID)
        return
    if parsed < date.today():
        await message.answer(ru.DATE_IN_PAST)
        return
    await state.update_data(date_from=parsed.isoformat())
    await state.set_state(VacationRequest.date_to)
    await message.answer(ru.VACATION_ASK_TO)


@router.message(VacationRequest.date_to, F.text)
async def set_date_to(message: Message, state: FSMContext) -> None:
    parsed = _parse_date(message.text)
    if parsed is None:
        await message.answer(ru.DATE_INVALID)
        return
    data = await state.get_data()
    if parsed < date.fromisoformat(data["date_from"]):
        await message.answer(ru.DATE_ORDER_INVALID)
        return
    await state.update_data(date_to=parsed.isoformat())
    await state.set_state(VacationRequest.comment)
    await message.answer(ru.VACATION_ASK_COMMENT)


@router.message(VacationRequest.comment, F.text)
async def set_comment(message: Message, state: FSMContext) -> None:
    comment = "" if message.text.strip() == "-" else message.text.strip()
    data = await state.update_data(comment=comment)

    date_from = date.fromisoformat(data["date_from"])
    date_to = date.fromisoformat(data["date_to"])
    await state.set_state(VacationRequest.confirm)
    await message.answer(
        ru.VACATION_CONFIRM.format(
            date_from=date_from.strftime(DATE_FMT),
            date_to=date_to.strftime(DATE_FMT),
            days=(date_to - date_from).days + 1,   # предварительно, точное число вернёт API
            comment=comment or "—",
        ),
        reply_markup=kb.confirm_inline("vacation:submit"),
    )


@router.callback_query(VacationRequest.confirm, F.data == "vacation:submit")
async def submit(callback: CallbackQuery, state: FSMContext,
                 client: BackendClient, token: str, role: str | None) -> None:
    data = await state.get_data()
    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        result = await client.create_vacation(
            token=token,
            date_from=data["date_from"],
            date_to=data["date_to"],
            comment=data.get("comment", ""),
        )
    except ApiError as exc:
        await state.clear()
        await callback.message.answer(exc.message, reply_markup=kb.main_menu(role))
        await callback.answer()
        return

    await state.clear()
    await callback.message.answer(ru.VACATION_SENT.format(id=result["id"]),
                                  reply_markup=kb.main_menu(role))
    await callback.answer()


# --- нетекстовый ввод внутри сценария: подсказка вместо молчания ---

@router.message(VacationRequest.date_from)
@router.message(VacationRequest.date_to)
async def wrong_date_input(message: Message) -> None:
    await message.answer(ru.DATE_INVALID)


@router.message(VacationRequest.comment)
async def wrong_comment_input(message: Message) -> None:
    await message.answer(ru.EXPECTED_TEXT)


@router.message(VacationRequest.confirm)
async def waiting_confirm(message: Message) -> None:
    await message.answer(ru.PRESS_BUTTON_ABOVE)


@router.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext, role: str | None) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(ru.CANCELLED, reply_markup=kb.main_menu(role))
    await callback.answer()
