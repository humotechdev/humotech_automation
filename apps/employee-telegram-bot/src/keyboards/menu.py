"""Клавиатуры. Состав меню зависит от роли, пришедшей с бэкенда."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# --- подписи кнопок (используются и в клавиатуре, и в фильтрах хендлеров) ---
BTN_PROFILE = "👤 Профиль"
BTN_ATTENDANCE = "🕘 Мои отметки"
BTN_STATISTICS = "📊 Статистика"
BTN_VACATION = "🏖 Отпуск"
BTN_SICK_LEAVE = "🤒 Больничный"
BTN_CORRECTION = "✏️ Исправить отметку"
BTN_QUESTION = "❓ Вопрос в HR"
BTN_HR_PANEL = "🧑‍💼 HR-панель"
BTN_CANCEL = "✖️ Отмена"
BTN_SHARE_PHONE = "📱 Отправить номер телефона"

remove = ReplyKeyboardRemove()


def main_menu(role: str | None) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_ATTENDANCE)],
        [KeyboardButton(text=BTN_STATISTICS), KeyboardButton(text=BTN_VACATION)],
        [KeyboardButton(text=BTN_SICK_LEAVE), KeyboardButton(text=BTN_CORRECTION)],
        [KeyboardButton(text=BTN_QUESTION)],
    ]
    if role in ("hr", "admin"):
        rows.append([KeyboardButton(text=BTN_HR_PANEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cancel_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True
    )


def share_phone_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SHARE_PHONE, request_contact=True)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def confirm_inline(yes_data: str, no_data: str = "cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Отправить", callback_data=yes_data),
        InlineKeyboardButton(text="✖️ Отмена", callback_data=no_data),
    ]])


def hr_panel_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Заявки на согласование",
                              callback_data="hr:requests")],
        [InlineKeyboardButton(text="📅 Кто на месте сегодня",
                              callback_data="hr:today")],
        [InlineKeyboardButton(text="🔎 Найти сотрудника",
                              callback_data="hr:search")],
    ])


def absence_decision_inline(absence_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить",
                             callback_data=f"absence:approve:{absence_id}"),
        InlineKeyboardButton(text="❌ Отклонить",
                             callback_data=f"absence:reject:{absence_id}"),
    ]])
