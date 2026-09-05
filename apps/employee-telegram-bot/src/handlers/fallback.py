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

from src.handlers.menu.router import build_menu
from src.keyboards import employee as kb
from src.messages import employee as text
from src.middlewares.employee import REASON_UNAVAILABLE

router = Router(name="fallback")

UNKNOWN = "Не понял. Выберите пункт меню ниже."
STALE_BUTTON = "Кнопка устарела. Откройте меню заново: /menu"


@router.message(StateFilter(None))
async def anything_else(message: Message, employee, denial) -> None:
    if employee is None:
        if denial == REASON_UNAVAILABLE:
            # Backend не ответил. Прежде здесь уходило «вы не привязаны»
            # вместе с клавиатурой «Помощь»: сетевой сбой на секунду
            # превращался в отказ в доступе, а меню человека — в одну
            # кнопку. Ни того, ни другого не происходило.
            await message.answer(text.BACKEND_DOWN)
            return
        await message.answer(text.NOT_LINKED, reply_markup=kb.help_only_menu())
        return
    await message.answer(UNKNOWN, reply_markup=build_menu(employee, message))


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer(STALE_BUTTON, show_alert=True)
