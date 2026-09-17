"""Меню сотрудника: два действия сверху, быстрые ответы под ними.

Правило разделения простое: то, на что есть короткий ответ, отвечается
прямо в чате; то, где надо что-то заполнить или навести камеру,
открывается в Mini App.

Верхний ряд — ДВЕ кнопки запуска приложения, `KeyboardButton` с
`web_app`. Отметка и кабинет: то, ради чего сюда заходят, в одно
нажатие и без промежуточных сообщений.

**Про подпись запуска — и почему запасной путь обязателен.**

Документация Bot API прямо говорит, что Mini App, открытый кнопкой
нижней клавиатуры, умеет только `Telegram.WebApp.sendData` — отправить
боту служебное сообщение. Подписанные данные о пользователе
(`user`, `auth_date`, `hash`, `query_id`) перечислены у inline-кнопки,
кнопки меню, вложений, прямой ссылки — но не у неё.

То же самое было записано здесь раньше и помечено как проверенное
живьём. Два независимых источника сходятся, и это значит: приложение,
открытое отсюда, скорее всего НЕ сможет доказать серверу, кто пришёл,
и покажет «откройте кнопкой в чате».

Кнопки всё равно собраны официальным способом — так просил владелец
продукта, и решает это живой телефон, а не документация. Но проверка
подписи из-за них не ослаблена ни на строку, а запасной путь
обязателен и работает всегда: команды `/scan` и `/cabinet` присылают
то же приложение inline-кнопкой, про которую сомнений нет. Тот же
текст показывает и сам экран `/scan`, если подписи не оказалось.

`web_app` у кнопки нижней клавиатуры Telegram разрешает ТОЛЬКО в личном
чате. В группе такая клавиатура — ошибка запроса, поэтому там верхний
ряд собирается текстовой кнопкой, как раньше.

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

#: Верхний ряд: две кнопки запуска Mini App.
BTN_SCAN = "📷 Отметиться"
BTN_OPEN = "👤 Кабинет"

#: Прежняя текстовая кнопка кабинета. Осталась для группового чата, где
#: `web_app` в нижней клавиатуре запрещён, и как запасной путь: по её
#: тексту бот присылает кабинет inline-кнопкой.
BTN_CABINET = "🏠 Открыть личный кабинет"
BTN_WHERE_AM_I = "📍 Я сейчас в офисе?"
BTN_TODAY = "📅 Сегодня"
BTN_WEEK = "🗓 За неделю"
BTN_MONTH = "📆 За месяц"
BTN_HISTORY = "🕘 История посещений"
BTN_SICK_LEAVE = "🤒 Больничный"
BTN_VACATION = "🏖 Отпуск"
BTN_MY_REQUESTS = "📄 Мои заявки"
BTN_ASK_HR = "✍️ Написать в HR"
BTN_HELP = "❓ Помощь"
#: Выход из ввода вопроса. В общем меню её нет: она нужна только там.
BTN_CANCEL = "✖️ Отмена"
BTN_SEND_LOCATION = "📍 Отправить геопозицию"

# Все подписи разом — по ним фильтруются хендлеры, и список должен быть один.
#
# Кнопки верхнего ряда сюда входят намеренно, хотя `web_app`-кнопка текста
# боту не шлёт: на клиенте, который её не открыл, человек всё равно
# нажмёт — и должен получить запасной путь, а не молчание.
ALL_BUTTONS = (
    BTN_SCAN, BTN_OPEN,
    BTN_CABINET, BTN_WHERE_AM_I, BTN_TODAY, BTN_WEEK, BTN_MONTH,
    BTN_HISTORY, BTN_SICK_LEAVE, BTN_VACATION, BTN_MY_REQUESTS, BTN_ASK_HR,
    BTN_HELP,
)

#: Путь быстрой отметки. Домен не хранится нигде в коде — приходит из
#: MINI_APP_URL. Единственное место, где адреса Mini App собираются:
#: второй такой сборщик рано или поздно разойдётся с этим.
SCAN_PATH = "/scan"

#: Метка транспорта, а НЕ авторизация. Она лишь переключает экран на
#: отдачу данных боту через `sendData` — подписи запуска у Mini App,
#: открытого нижней кнопкой, нет, и доказать личность оттуда нечем.
#: Подставить её в адрес может кто угодно; личность всё равно
#: устанавливает бот по подтверждённому Telegram `message.from.id`.
KEYBOARD_SOURCE = "?source=keyboard"


def scan_url(mini_app_url: str, *, from_keyboard: bool = False) -> str:
    """Адрес быстрой отметки. Хвостовая косая черта у базы допустима."""
    base = mini_app_url.rstrip("/") + SCAN_PATH
    return base + KEYBOARD_SOURCE if from_keyboard else base


def cabinet_url(mini_app_url: str) -> str:
    """Адрес кабинета: корень приложения, ровно как в настройке."""
    return mini_app_url.rstrip("/") + "/"


def employee_menu(
    mini_app_url: str | None,
    *,
    private: bool = True,
    launch_apps: bool = True,
) -> ReplyKeyboardMarkup:
    """Меню сотрудника с рабочей привязкой. Единственный сборщик на бота.

    Без адреса Mini App верхний ряд не показывается вовсе: кнопка,
    которая ничего не открывает, хуже её отсутствия.

    `private=False` — групповой чат. Там `web_app` в нижней клавиатуре
    запрещён Telegram, и ряд собирается прежней текстовой кнопкой:
    иначе Telegram отклонит весь запрос целиком, и человек останется
    вообще без клавиатуры.

    `is_persistent` — чтобы клавиатура не сворачивалась в значок после
    первого же ответа: она здесь постоянный инструмент, а не разовый
    вопрос. `one_time_keyboard` по той же причине выключен явно.

    `launch_apps=False` — те же две подписи, но обычными текстовыми
    кнопками. Нужно там, где клиент не показал клавиатуру с `web_app`:
    подписи и обработчики остаются прежними, путь становится на одно
    нажатие длиннее, но кнопки есть.
    """
    rows = []
    if mini_app_url:
        rows.append(
            _launch_row(mini_app_url, private=private, launch_apps=launch_apps)
        )
    rows.extend(
        [
            [KeyboardButton(text=BTN_WHERE_AM_I)],
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_WEEK),
             KeyboardButton(text=BTN_MONTH)],
            [KeyboardButton(text=BTN_HISTORY)],
            [KeyboardButton(text=BTN_SICK_LEAVE),
             KeyboardButton(text=BTN_VACATION)],
            [KeyboardButton(text=BTN_MY_REQUESTS), KeyboardButton(text=BTN_ASK_HR)],
            [KeyboardButton(text=BTN_HELP)],
        ]
    )
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
    )


def cancel_menu() -> ReplyKeyboardMarkup:
    """Клавиатура на время ввода вопроса: только выход из него."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def location_request() -> ReplyKeyboardMarkup:
    """Клавиатура после скана печатного QR: геопозиция или отмена.

    Геопозицию отправляет сам Telegram по нажатию — текущую, с
    погрешностью. Набрать координаты руками здесь нельзя, и это
    намеренно.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SEND_LOCATION, request_location=True)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _launch_row(
    mini_app_url: str, *, private: bool, launch_apps: bool = True
) -> list[KeyboardButton]:
    """Верхний ряд: отметка и кабинет одним нажатием."""
    if not private:
        return [KeyboardButton(text=BTN_CABINET)]
    if not launch_apps:
        # Тот же ряд без `web_app`. По этим подписям уже есть обработчики:
        # человек получит inline-кнопку вместо мгновенного запуска.
        return [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_OPEN)]
    return [
        KeyboardButton(
            text=BTN_SCAN,
            web_app=WebAppInfo(url=scan_url(mini_app_url, from_keyboard=True)),
        ),
        KeyboardButton(
            text=BTN_OPEN, web_app=WebAppInfo(url=cabinet_url(mini_app_url))
        ),
    ]


def cabinet_button(mini_app_url: str) -> InlineKeyboardMarkup:
    """Кабинет inline-кнопкой — запасной путь, в котором нет сомнений.

    Приложение, открытое отсюда, точно получает подписанную Telegram
    строку запуска. Верхний ряд нижней клавиатуры делает то же самое
    и на одно нажатие короче, но проверяется он только на живом
    телефоне — поэтому этот путь остаётся.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CABINET,
                                  web_app=WebAppInfo(url=mini_app_url))]
        ]
    )


def scan_button(mini_app_url: str) -> InlineKeyboardMarkup:
    """Отметка inline-кнопкой. Запасной путь к тому же экрану `/scan`."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=BTN_SCAN, web_app=WebAppInfo(url=scan_url(mini_app_url))
            )]
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
    "KEYBOARD_SOURCE",
    "SCAN_PATH",
    "cabinet_button",
    "cabinet_url",
    "scan_button",
    "scan_url",
    "BTN_CABINET",
    "BTN_OPEN",
    "BTN_SCAN",
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
