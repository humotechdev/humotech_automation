"""Очередь заявок на согласование: одобрить / отклонить.

Решение принимает бэкенд. Если заявку уже обработал другой HR, приходит 409 —
бот обязан честно об этом сказать, а не делать вид, что операция удалась.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.api import BackendClient
from src.api.errors import ApiError, Conflict
from src.keyboards import menu as kb
from src.messages import ru
from src.states.flows import HrRejectAbsence

router = Router(name="hr:requests")

TYPE_META = {
    "vacation": ("🏖", "Отпуск"),
    "sick_leave": ("🤒", "Больничный"),
}


def _card(item: dict) -> str:
    icon, type_name = TYPE_META.get(item["type"], ("📄", item["type"]))
    employee = item.get("employee") or {}
    return ru.HR_REQUEST_CARD.format(
        icon=icon,
        type_name=type_name,
        id=item["id"],
        full_name=employee.get("full_name", "—"),
        position=employee.get("position", "—"),
        office=employee.get("office", "—"),
        date_from=item["date_from"],
        date_to=item["date_to"],
        days=item["days"],
        comment=item.get("comment") or "—",
    )


@router.message(Command("requests"))
@router.callback_query(F.data == "hr:requests")
async def show_pending(event: Message | CallbackQuery, client: BackendClient,
                       token: str) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    try:
        data = await client.hr_absences_pending(token)
    except ApiError as exc:
        await message.answer(exc.message)
        if isinstance(event, CallbackQuery):
            await event.answer()
        return

    if not data["items"]:
        await message.answer(ru.HR_NO_REQUESTS)
    else:
        for item in data["items"]:
            await message.answer(_card(item),
                                 reply_markup=kb.absence_decision_inline(item["id"]))

    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data.startswith("absence:approve:"))
async def approve(callback: CallbackQuery, client: BackendClient,
                  token: str) -> None:
    absence_id = int(callback.data.split(":")[2])
    try:
        await client.hr_approve_absence(token, absence_id)
    except Conflict:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(ru.HR_ALREADY_DECIDED.format(id=absence_id))
        await callback.answer()
        return
    except ApiError as exc:
        await callback.answer(exc.message, show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(ru.HR_APPROVED.format(id=absence_id))
    await callback.answer()


@router.callback_query(F.data.startswith("absence:reject:"))
async def ask_reason(callback: CallbackQuery, state: FSMContext) -> None:
    absence_id = int(callback.data.split(":")[2])
    await state.set_state(HrRejectAbsence.reason)
    await state.update_data(absence_id=absence_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(ru.HR_ASK_REJECT_REASON.format(id=absence_id),
                                  reply_markup=kb.cancel_menu())
    await callback.answer()


@router.message(HrRejectAbsence.reason, F.text)
async def do_reject(message: Message, state: FSMContext, client: BackendClient,
                    token: str, role: str) -> None:
    data = await state.get_data()
    absence_id = data["absence_id"]
    await state.clear()

    try:
        await client.hr_reject_absence(token, absence_id, message.text.strip())
    except Conflict:
        await message.answer(ru.HR_ALREADY_DECIDED.format(id=absence_id),
                             reply_markup=kb.main_menu(role))
        return
    except ApiError as exc:
        await message.answer(exc.message, reply_markup=kb.main_menu(role))
        return

    await message.answer(ru.HR_REJECTED.format(id=absence_id),
                         reply_markup=kb.main_menu(role))


@router.message(HrRejectAbsence.reason)
async def wrong_reason_input(message: Message) -> None:
    await message.answer(ru.EXPECTED_TEXT)
