"""Ответ сотрудника на запрос HR о работе вне офиса.

Кнопка содержит только UUID запроса и выбор. Подтверждённый Telegram ID
берётся из callback.from_user, после чего backend заново проверяет
актуальную привязку и принадлежность запроса. Старые и повторные кнопки
безопасны: решение о состоянии и идемпотентности принимает backend.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.notifications.buttons import FIELD_WORK_CONFIRM, FIELD_WORK_DECLINE
from src.utils.safe import is_uuid

logger = logging.getLogger(__name__)
router = Router(name="field-work")

CONFIRMED = "✅ Работа вне офиса подтверждена. День будет учтён по вашему графику."
DECLINED = "Ваш ответ «Не подтверждаю» передан HR. Рабочий день автоматически не засчитан."
CLOSED = "Запрос уже закрыт или срок ответа истёк. Обратитесь к HR при необходимости."
CONFLICT = "Сейчас подтвердить нельзя: изменилась отметка или график. Обратитесь к HR."
NOT_AVAILABLE = "Запрос недоступен. Если это ошибка, обратитесь к HR."
RETRY = "Не удалось сохранить ответ. Попробуйте нажать кнопку ещё раз позже."
INVALID = "Эта кнопка недействительна. Обратитесь к HR."


@router.callback_query(
    F.data.startswith(FIELD_WORK_CONFIRM) | F.data.startswith(FIELD_WORK_DECLINE)
)
async def decide(call: CallbackQuery, client: SelfServiceClient) -> None:
    """Сохранить ответ через backend и снять кнопки лишь после окончательного ответа."""
    data = call.data or ""
    yes = data.startswith(FIELD_WORK_CONFIRM)
    prefix = FIELD_WORK_CONFIRM if yes else FIELD_WORK_DECLINE
    request_id = data[len(prefix):]
    user = call.from_user
    message = call.message
    if not is_uuid(request_id) or user is None or message is None:
        await call.answer(INVALID, show_alert=True)
        return
    # Этот сценарий отправляется в личный чат. Групповые callbacks, даже
    # если туда скопировали клавиатуру, не должны принимать решения.
    if message.chat.type != "private" or message.chat.id != user.id:
        await call.answer(NOT_AVAILABLE, show_alert=True)
        return

    # Длительный запрос к backend не должен оставлять вращающуюся кнопку.
    await call.answer("Сохраняем ответ…")
    try:
        result = await client.field_work_decision(
            telegram_user_id=user.id,
            request_id=request_id,
            decision="CONFIRM" if yes else "DECLINE",
        )
    except ApiError as error:
        logger.info("field work decision refused: status=%s code=%s", error.status, error.code)
        if error.status == 409:
            details = error.details if isinstance(error.details, dict) else {}
            if details.get("reason") == "closed_or_expired":
                await _finish(message, CLOSED)
            else:
                # Если график или посещаемость изменились, запрос может
                # ещё оставаться PENDING; не прятать кнопки до решения HR.
                await message.answer(CONFLICT, parse_mode=None)
        elif error.status == 404:
            await _finish(message, NOT_AVAILABLE)
        else:
            await message.answer(RETRY, parse_mode=None)
        return
    except Exception:
        logger.exception("field work decision unavailable")
        await message.answer(RETRY, parse_mode=None)
        return

    status = str(result.get("status") or "").upper()
    if status == "CONFIRMED":
        await _finish(message, CONFIRMED)
    elif status == "DECLINED":
        await _finish(message, DECLINED)
    else:
        # Не показывать успех по неожиданному ответу сервера.
        logger.warning("unexpected field work decision status: %s", status)
        await message.answer(RETRY, parse_mode=None)


async def _finish(message, text: str) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        # Само решение уже принято; ошибка Telegram при удалении старых
        # кнопок не должна скрывать результат. Повтор решит backend.
        logger.info("could not clear field work buttons")
    await message.answer(text, parse_mode=None)


__all__ = ["router", "decide"]
