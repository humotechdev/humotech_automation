"""Меню сотрудника. Десять пунктов, никаких подменю глубже одного уровня.

Правило разделения простое: то, на что есть короткий ответ, отвечается
прямо в чате; то, где надо что-то заполнить, открывается в Mini App.
Заставлять человека вводить даты сообщениями в чат — способ получить
«3 сентебря» и три уточняющих вопроса.

Кнопка личного кабинета — обычная текстовая, и она первая: это главный
вход. Само приложение открывается ОТДЕЛЬНОЙ inline-кнопкой, которую бот
присылает в ответ, — и это не украшение.

Telegram передаёт подписанные данные о пользователе только приложениям,
открытым из inline-кнопки, кнопки меню или прямой ссылки. Приложение,
открытое из нижней клавиатуры, не получает ничего: ни подписи, ни имени.
Оно и не должно — такие Mini App умеют только отправить ответ боту через
`sendData`. Кабинету этого мало: ему надо знать, кто пришёл.

Остальные кнопки — обычные текстовые: их видно в истории чата, и по ним
можно вернуться к прошлому ответу, не нажимая заново.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

BTN_CABINET = "🏠 Открыть личный кабинет"
BTN_WHERE_AM_I = "📍 Я сейчас в офисе?"
BTN_TODAY = "📅 Сегодня"
BTN_WEEK = "🗓 За неделю"
BTN_MONTH = "📆 За месяц"
BTN_HISTORY = "🕘 История посещений"
BTN_SICK_LEAVE = "🤒 Больничный"
BTN_VACATION = "🏖 Отпуск"
BTN_MY_REQUESTS = "📄 Мои заявки"
BTN_HELP = "❓ Помощь"

# Все подписи разом — по ним фильтруются хендлеры, и список должен быть один.
ALL_BUTTONS = (
    BTN_CABINET, BTN_WHERE_AM_I, BTN_TODAY, BTN_WEEK, BTN_MONTH,
    BTN_HISTORY, BTN_SICK_LEAVE, BTN_VACATION, BTN_MY_REQUESTS, BTN_HELP,
)


def employee_menu(mini_app_url: str | None) -> ReplyKeyboardMarkup:
    """Меню сотрудника с рабочей привязкой.

    Без адреса Mini App кнопка кабинета не показывается вовсе: кнопка,
    которая ничего не открывает, хуже её отсутствия.
    """
    rows = []
    if mini_app_url:
        rows.append([KeyboardButton(text=BTN_CABINET)])
    rows.extend(
        [
            [KeyboardButton(text=BTN_WHERE_AM_I)],
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_WEEK),
             KeyboardButton(text=BTN_MONTH)],
            [KeyboardButton(text=BTN_HISTORY)],
            [KeyboardButton(text=BTN_SICK_LEAVE),
             KeyboardButton(text=BTN_VACATION)],
            [KeyboardButton(text=BTN_MY_REQUESTS), KeyboardButton(text=BTN_HELP)],
        ]
    )
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cabinet_button(mini_app_url: str) -> InlineKeyboardMarkup:
    """Единственная кнопка, которой кабинет открывается по-настоящему.

    Именно inline: приложение, открытое отсюда, получает подписанную
    Telegram строку запуска и может доказать backend, кто пришёл.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CABINET,
                                  web_app=WebAppInfo(url=mini_app_url))]
        ]
    )


def help_only_menu() -> ReplyKeyboardMarkup:
    """Меню для того, у кого доступа нет.

    Одна кнопка. Показывать разделы, которые всё равно ответят отказом, —
    значит обещать то, чего нет: человек будет нажимать и получать отказ
    за отказом вместо одного понятного объяснения.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_HELP)]], resize_keyboard=True
    )


__all__ = [
    "ALL_BUTTONS",
    "cabinet_button",
    "BTN_CABINET",
    "BTN_HELP",
    "BTN_HISTORY",
    "BTN_MONTH",
    "BTN_MY_REQUESTS",
    "BTN_SICK_LEAVE",
    "BTN_TODAY",
    "BTN_VACATION",
    "BTN_WEEK",
    "BTN_WHERE_AM_I",
    "employee_menu",
    "help_only_menu",
]
