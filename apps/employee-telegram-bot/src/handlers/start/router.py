"""/start, привязка аккаунта, главное меню.

Порядок регистрации внутри роутера имеет значение: «Отмена» и команды идут
ПЕРЕД обработчиками состояний, иначе текст «✖️ Отмена» или «/menu» будет принят
за номер телефона или код подтверждения.
"""

from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.api import BackendClient, TokenStorage
from src.api.errors import ApiError
from src.config.settings import settings
from src.keyboards import menu as kb
from src.messages import ru
from src.states.flows import LinkAccount
from src.utils.commands import sync_commands_for_chat

router = Router(name="start")

PHONE_RE = re.compile(r"^\+?\d{9,15}$")


def _normalize_phone(raw: str) -> str | None:
    cleaned = re.sub(r"[\s\-()]", "", raw.strip())
    return "+" + cleaned.lstrip("+") if PHONE_RE.match(cleaned) else None


def _menu_for(role: str | None):
    """Непривязанному человеку клавиатуру сотрудника показывать нельзя."""
    return kb.main_menu(role) if role else kb.remove


# --- глобальные выходы: регистрируются раньше любых состояний ---

@router.message(F.text == kb.BTN_CANCEL)
async def cancel_any(message: Message, state: FSMContext, role: str | None) -> None:
    await state.clear()
    await message.answer(ru.CANCELLED, reply_markup=_menu_for(role))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, role: str | None,
                    employee: dict | None, bot: Bot) -> None:
    await state.clear()

    if role is not None:
        await sync_commands_for_chat(bot, message.chat.id, role)
        await message.answer(
            ru.WELCOME_BACK.format(full_name=employee["full_name"]),
            reply_markup=kb.main_menu(role),
        )
        return

    text = ru.WELCOME_GUEST + (ru.STUB_HINT if settings.api_mode == "stub" else "")
    await state.set_state(LinkAccount.phone)
    await message.answer(text, reply_markup=kb.share_phone_menu())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, role: str | None,
                   employee: dict | None) -> None:
    await state.clear()
    if role is None:
        await message.answer(ru.NOT_LINKED, reply_markup=kb.remove)
        return
    await message.answer(
        ru.WELCOME_BACK.format(full_name=employee["full_name"]),
        reply_markup=kb.main_menu(role),
    )


# --- привязка аккаунта ---

@router.message(LinkAccount.phone, F.contact)
async def link_phone_from_contact(message: Message, state: FSMContext,
                                  client: BackendClient) -> None:
    await _send_code(message, state, client, message.contact.phone_number)


@router.message(LinkAccount.phone, F.text)
async def link_phone_from_text(message: Message, state: FSMContext,
                               client: BackendClient) -> None:
    phone = _normalize_phone(message.text)
    if phone is None:
        await message.answer(ru.PHONE_INVALID)
        return
    await _send_code(message, state, client, phone)


@router.message(LinkAccount.phone)
async def link_phone_other(message: Message) -> None:
    """Стикер, фото, геопозиция — что угодно, кроме текста и контакта."""
    await message.answer(ru.PHONE_INVALID)


async def _send_code(message: Message, state: FSMContext,
                     client: BackendClient, raw_phone: str) -> None:
    phone = _normalize_phone(raw_phone)
    if phone is None:
        await message.answer(ru.PHONE_INVALID)
        return
    try:
        await client.request_code(phone, message.from_user.id)
    except ApiError as exc:
        await message.answer(exc.message)
        return

    await state.update_data(phone=phone)
    await state.set_state(LinkAccount.code)
    await message.answer(ru.ENTER_CODE.format(phone=phone),
                         reply_markup=kb.cancel_menu())


@router.message(LinkAccount.code, F.text)
async def link_code(message: Message, state: FSMContext, bot: Bot,
                    client: BackendClient, tokens: TokenStorage) -> None:
    code = message.text.strip()
    if not (code.isdigit() and len(code) == 6):
        await message.answer(ru.CODE_INVALID)
        return

    data = await state.get_data()
    try:
        result = await client.link(
            phone=data["phone"],
            code=code,
            telegram_id=message.from_user.id,
            telegram_username=message.from_user.username,
        )
    except ApiError as exc:
        await message.answer(exc.message)
        return

    await tokens.set(message.from_user.id, result["access_token"])
    await state.clear()

    employee = result["employee"]
    await sync_commands_for_chat(bot, message.chat.id, employee["role"])
    await message.answer(
        ru.LINK_SUCCESS.format(full_name=employee["full_name"]),
        reply_markup=kb.main_menu(employee["role"]),
    )


@router.message(LinkAccount.code)
async def link_code_other(message: Message) -> None:
    await message.answer(ru.CODE_INVALID)
