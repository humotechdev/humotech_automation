"""HR-панель: сводка по офису и поиск сотрудника."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.api import BackendClient
from src.api.errors import ApiError
from src.keyboards import menu as kb
from src.messages import ru
from src.states.flows import HrSearchEmployee

router = Router(name="hr:panel")


@router.message(Command("hr"))
@router.message(F.text == kb.BTN_HR_PANEL)
async def open_panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(ru.HR_PANEL, reply_markup=kb.hr_panel_inline())


@router.message(Command("today"))
@router.callback_query(F.data == "hr:today")
async def today(event: Message | CallbackQuery, client: BackendClient,
                token: str) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    try:
        data = await client.hr_attendance_today(token)
    except ApiError as exc:
        await message.answer(exc.message)
        if isinstance(event, CallbackQuery):
            await event.answer()
        return

    text = ru.HR_TODAY.format(
        office=(data.get("office") or {}).get("name", "—"),
        date=data["date"],
        total_employees=data["total_employees"],
        present=data["present"],
        late=data["late"],
        absent=data["absent"],
        on_vacation=data["on_vacation"],
        on_sick_leave=data["on_sick_leave"],
    )
    if data.get("late_list"):
        lines = "\n".join(
            f"• {row['full_name']} — +{row['late_minutes']} мин"
            for row in data["late_list"]
        )
        text += ru.HR_LATE_LIST.format(lines=lines)

    await message.answer(text)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "hr:search")
async def ask_query(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(HrSearchEmployee.query)
    await callback.message.answer(ru.HR_ASK_QUERY, reply_markup=kb.cancel_menu())
    await callback.answer()


@router.message(HrSearchEmployee.query, F.text)
async def run_search(message: Message, state: FSMContext,
                     client: BackendClient, token: str, role: str) -> None:
    query = message.text.strip()
    await state.clear()
    try:
        data = await client.hr_employees(token, query)
    except ApiError as exc:
        await message.answer(exc.message, reply_markup=kb.main_menu(role))
        return

    if not data["items"]:
        await message.answer(ru.HR_NO_EMPLOYEES.format(query=query),
                             reply_markup=kb.main_menu(role))
        return

    lines = "\n".join(
        ru.HR_EMPLOYEE_LINE.format(
            full_name=row["full_name"], position=row["position"], office=row["office"]
        )
        for row in data["items"]
    )
    await message.answer(lines, reply_markup=kb.main_menu(role))


@router.message(HrSearchEmployee.query)
async def wrong_query_input(message: Message) -> None:
    await message.answer(ru.EXPECTED_TEXT)
