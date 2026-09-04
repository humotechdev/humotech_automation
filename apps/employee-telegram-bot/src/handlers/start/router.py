"""/start и привязка по одноразовой ссылке.

Привязку начинает HR: он выдаёт сотруднику персональную ссылку вида
`https://t.me/<бот>?start=link_<ТОКЕН>`. Бот участвует ровно одним вызовом —
сообщает backend токен и подтверждённый Telegram-аккаунт. Решение принимает
backend, бот только пересказывает ответ человеку.

Бизнес-логики привязки здесь нет и быть не должно. Handler разбирает
полезную нагрузку `/start`, вызывает клиента и подбирает текст под код
ответа. Всё остальное — срок, одноразовость, отзыв, чей это сотрудник —
решается там, где есть база и права.

Порядок регистрации внутри роутера имеет значение: обработчик ссылки идёт
ПЕРЕД обычным `/start`, иначе полезная нагрузка потеряется.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.keyboards import employee as kb
from src.messages import link as text

logger = logging.getLogger(__name__)

router = Router(name="start")

# Префикс полезной нагрузки `/start`. Telegram отдаёт в ней до 64 символов
# из [A-Za-z0-9_-], чего хватает на «link_» плюс токен в 43 символа.
LINK_PREFIX = "link_"

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


@router.message(CommandStart(deep_link=True))
async def start_with_link(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    client: SelfServiceClient,
    employee,
    denial,
) -> None:
    token = parse_link_payload(command.args)
    if token is None:
        # Нагрузка есть, но не наша: ведём себя как при обычном /start.
        # Причину отказа передаём дальше — без неё человек без привязки
        # получил бы «доступ закрыт» вместо «попросите ссылку».
        from src.handlers.menu.router import start as plain_start

        await plain_start(message, employee, denial)
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
    except ApiError as error:
        reason = (error.details or {}).get("reason") if error.details else None
        # Токена в журнале нет: он рабочий секрет, пока ссылка жива.
        logger.info("link attempt refused for %s: %s", user.id, reason or error.code)
        await message.answer(
            text.LINK_MESSAGES.get(reason, text.LINK_ERROR),
            reply_markup=kb.help_only_menu(),
        )
        return

    # Успех — это ещё НЕ доступ: привязка ждёт подтверждения HR.
    # Клавиатуру сотрудника здесь показывать нельзя.
    await message.answer(text.LINK_PENDING, reply_markup=kb.help_only_menu())


__all__ = ["LINK_PREFIX", "parse_link_payload", "router"]
