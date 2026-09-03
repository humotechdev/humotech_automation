"""Последний роутер в цепочке: ни один апдейт не должен остаться без ответа.

Зачем это нужно. Reply-клавиатура живёт в Telegram у пользователя и переживает
рестарт бота, а токены сейчас хранятся в памяти процесса. После перезапуска
человек жмёт привычную кнопку — и без этого роутера не получает ничего.
То же с устаревшими inline-кнопками: без answer() у пользователя вечно крутится
индикатор загрузки.

Роутер включается ПОСЛЕДНИМ, поэтому перехватывает только то, что не разобрали
остальные. StateFilter(None) защищает активные FSM-сценарии.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from src.keyboards import menu as kb
from src.messages import ru
from src.utils.filters import IsLinked, IsNotLinked

router = Router(name="fallback")


@router.message(StateFilter(None), IsNotLinked())
async def unlinked_anything(message: Message) -> None:
    await message.answer(ru.NOT_LINKED, reply_markup=kb.remove)


@router.message(StateFilter(None), IsLinked())
async def linked_unknown(message: Message, role: str) -> None:
    await message.answer(ru.UNKNOWN_COMMAND, reply_markup=kb.main_menu(role))


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer(ru.STALE_BUTTON, show_alert=True)
