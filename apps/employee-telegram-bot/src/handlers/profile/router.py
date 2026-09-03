"""Профиль сотрудника. Все данные приходят из GET /employees/me."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.keyboards import menu as kb
from src.messages import ru
from src.utils.filters import IsLinked

router = Router(name="profile")
router.message.filter(IsLinked())


@router.message(Command("profile"))
@router.message(F.text == kb.BTN_PROFILE)
async def show_profile(message: Message, employee: dict) -> None:
    await message.answer(
        ru.PROFILE.format(
            full_name=employee["full_name"],
            position=employee.get("position") or "—",
            department=(employee.get("department") or {}).get("name", "—"),
            office=(employee.get("office") or {}).get("name", "—"),
            hired_at=employee.get("hired_at") or "—",
            role=ru.ROLE_NAMES.get(employee["role"], employee["role"]),
        )
    )
