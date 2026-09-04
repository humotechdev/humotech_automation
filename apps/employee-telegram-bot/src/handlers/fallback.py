"""Последний роутер в цепочке: ни один апдейт не должен остаться без ответа.

Reply-клавиатура живёт в Telegram у пользователя и переживает рестарт бота.
Человек жмёт привычную кнопку от прошлой версии меню — и без этого роутера
не получает ничего. То же с устаревшими inline-кнопками: без `answer()`
у пользователя вечно крутится индикатор загрузки.

Включается ПОСЛЕДНИМ, поэтому перехватывает только то, что не разобрали
остальные. `StateFilter(None)` защищает активные сценарии.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from src.config.settings import settings
from src.keyboards import employee as kb
from src.messages import employee as text

router = Router(name="fallback")

UNKNOWN = "Не понял. Выберите пункт меню ниже."
STALE_BUTTON = "Кнопка устарела. Откройте меню заново: /menu"


@router.message(StateFilter(None))
async def anything_else(message: Message, employee) -> None:
    if employee is None:
        await message.answer(text.NOT_LINKED, reply_markup=kb.help_only_menu())
        return
    await message.answer(
        UNKNOWN, reply_markup=kb.employee_menu(settings.mini_app_url)
    )


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer(STALE_BUTTON, show_alert=True)
