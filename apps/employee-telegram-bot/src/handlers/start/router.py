"""/start, привязка по одноразовой ссылке и главное меню.

Привязку начинает HR: он выдаёт сотруднику персональную ссылку вида
`https://t.me/<бот>?start=link_<ТОКЕН>`. Бот в этом участвует ровно одним
вызовом — сообщает backend токен и подтверждённый Telegram-аккаунт.
Решение принимает backend, бот только пересказывает ответ человеку.

Бизнес-логики привязки здесь нет и быть не должно. Handler разбирает
полезную нагрузку `/start`, вызывает клиента и подбирает текст под код
ответа — всё остальное (срок, одноразовость, отзыв, чей это сотрудник)
решается на стороне backend, где есть база и права.

Порядок регистрации внутри роутера имеет значение: обработчик ссылки идёт
ПЕРЕД обычным `/start`, иначе полезная нагрузка потеряется.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.api import BackendClient
from src.api.errors import ApiError
from src.config.settings import settings
from src.keyboards import menu as kb
from src.messages import ru
from src.utils.commands import sync_commands_for_chat

logger = logging.getLogger(__name__)

router = Router(name="start")

# Префикс полезной нагрузки `/start`. Telegram отдаёт в ней до 64 символов
# из [A-Za-z0-9_-], чего хватает на «link_» плюс токен в 43 символа.
LINK_PREFIX = "link_"

# Код причины из ответа backend -> что сказать человеку.
# Причина приходит в `error.details.reason`; текст ответа не разбирается —
# он переводится и переписывается, а код нет.
LINK_MESSAGES = {
    "expired": ru.LINK_EXPIRED,
    "revoked": ru.LINK_REVOKED,
    "used": ru.LINK_ALREADY_USED,
    "pending": ru.LINK_AWAITING_CONFIRMATION,
    "invalid": ru.LINK_INVALID,
    "telegram_taken": ru.LINK_TELEGRAM_TAKEN,
    "employee_inactive": ru.LINK_EMPLOYEE_INACTIVE,
}


def parse_link_payload(payload: str | None) -> str | None:
    """Токен из полезной нагрузки `/start`, либо None.

    Разбор строгий: посторонняя нагрузка не должна случайно оказаться
    «почти токеном» и уйти на backend.
    """
    if not payload:
        return None
    payload = payload.strip()
    if not payload.startswith(LINK_PREFIX):
        return None
    return payload[len(LINK_PREFIX):] or None


def _menu_for(role: str | None):
    """Непривязанному человеку клавиатуру сотрудника показывать нельзя."""
    return kb.main_menu(role) if role else kb.remove


# --- глобальные выходы: регистрируются раньше любых состояний ---

@router.message(F.text == kb.BTN_CANCEL)
async def cancel_any(message: Message, state: FSMContext, role: str | None) -> None:
    await state.clear()
    await message.answer(ru.CANCELLED, reply_markup=_menu_for(role))


# --- привязка по ссылке ---
#
# Идёт ПЕРЕД обычным /start: иначе полезная нагрузка достанется общему
# обработчику и потеряется.

@router.message(CommandStart(deep_link=True))
async def cmd_start_with_payload(
    message: Message, command: CommandObject, state: FSMContext,
    role: str | None, employee: dict | None, bot: Bot,
    client: BackendClient,
) -> None:
    token = parse_link_payload(command.args)
    if token is None:
        # Нагрузка есть, но не наша: ведём себя как при обычном /start.
        await cmd_start(message, state, role, employee, bot)
        return

    await state.clear()
    user = message.from_user
    try:
        await client.consume_link_token(
            token=token,
            telegram_user_id=user.id,
            telegram_chat_id=message.chat.id,
            telegram_username=user.username,
            language_code=user.language_code,
        )
    except ApiError as exc:
        reason = (exc.details or {}).get("reason") if exc.details else None
        # Токен в журнал не пишем: он рабочий секрет, пока ссылка жива.
        logger.info("link attempt refused for %s: %s", user.id, reason or exc.code)
        await message.answer(
            LINK_MESSAGES.get(reason, ru.ERR_SERVER), reply_markup=kb.remove
        )
        return

    # Успех — это ещё НЕ доступ: привязка ждёт подтверждения HR.
    # Клавиатуру сотрудника здесь показывать нельзя.
    await message.answer(ru.LINK_PENDING, reply_markup=kb.remove)


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

    # Своими силами привязаться нельзя: ссылку выдаёт HR конкретному человеку.
    text = ru.WELCOME_GUEST + (ru.STUB_HINT if settings.api_mode == "stub" else "")
    await message.answer(text, reply_markup=kb.remove)


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
