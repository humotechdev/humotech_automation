"""Клавиатуры первичного ознакомления.

Подписи кнопок подтверждения приходят с сервера (`button_label`,
`agree_label`): под правилами распорядка стоит «С правилами ознакомился»,
под последней карточкой — «Завершить ознакомление», и решает это тот, кто
пишет текст, а не тот, кто рисует клавиатуру.

**Про длину callback data.** Telegram отводит под неё 64 байта. Здесь
префикс из двух-трёх символов плюс UUID (36) — сорок с небольшим, с
запасом. Класть туда что-то ещё нельзя: данные живут в базе, а кнопка
только называет запись.

Своей нижней клавиатуры у ознакомления нет. Когда-то была — три
пункта вместо одиннадцати, — но это имело смысл ровно до тех пор, пока
незавершённое ознакомление закрывало рабочие разделы. Оно их больше не
закрывает: человек отмечается и подаёт заявки с первого дня, и прятать
от него меню не за чем. Остался один пункт, который добавляется к
обычному меню, пока дело не доделано.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

#: Пункт, который добавляется к обычному меню, пока ознакомление не
#: завершено. Подпись постоянная: по ней фильтруется обработчик, а
#: число пройденных разделов живёт в напоминании, где оно и уместно.
BTN_CONTINUE = "▶️ Продолжить ознакомление"
BTN_RULES = "📘 Правила и документы"

#: Коды нажатий. Короткие намеренно — см. пояснение выше.
START = "ob:go"
LATER = "ob:later"
DOCS = "ob:docs"
MENU = "ob:menu"
ACK = "oa:"      # + идентификатор раздела
BACK = "os:"     # + номер предыдущего раздела
OPEN = "op:"     # + идентификатор редакции: открыть полный текст
AGREE = "oy:"    # + идентификатор редакции: согласен
REFUSE = "on:"   # + идентификатор редакции: не согласен
DOC = "od:"      # + идентификатор редакции: открыть карточку документа

#: Подписи, по которым фильтруются обработчики ознакомления.
ONBOARDING_BUTTONS = (BTN_CONTINUE, BTN_RULES)


def nudge(state: dict) -> InlineKeyboardMarkup:
    """Напоминание под меню: одна кнопка с числом пройденного.

    Текст у кнопки меняется, код нажатия — нет. Так и должно быть:
    «3 из 10» это состояние, и зашивать его в `callback_data` значило
    бы, что нажатая завтра кнопка сообщит серверу вчерашнее число.

    Показывается ровно то, на чём человек стоит. «0 из 3 документов»
    тому, кто ещё читает карточки, не говорит ничего — он до них не
    дошёл.
    """
    if state.get("status") == "UPDATE_REQUIRED":
        title = "Подтвердить новую редакцию документа"
    elif not state.get("info_completed"):
        title = (
            f"Пройти ознакомление · {state.get('sections_done', 0)} "
            f"из {state.get('sections_total', 0)}"
        )
    else:
        title = (
            f"Подтвердить документы · {state.get('policies_done', 0)} "
            f"из {state.get('policies_total', 0)}"
        )
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=title, callback_data=START)
    ]])


def welcome() -> InlineKeyboardMarkup:
    """«Начать ознакомление» — единственная кнопка приветствия."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Начать ознакомление", callback_data=START)
    ]])


def card(section: dict) -> InlineKeyboardMarkup:
    """Кнопки под информационной карточкой.

    «← Назад» появляется со второй: под первой ей некуда вести, и
    неработающая кнопка учит не доверять остальным.
    """
    row: list[InlineKeyboardButton] = []
    if section["position"] > 1:
        row.append(InlineKeyboardButton(
            text="← Назад", callback_data=f"{BACK}{section['position'] - 1}"
        ))
    row.append(InlineKeyboardButton(
        text=section["button_label"], callback_data=f"{ACK}{section['id']}"
    ))
    return InlineKeyboardMarkup(inline_keyboard=[
        row,
        [InlineKeyboardButton(text="Прочитать позже", callback_data=LATER)],
    ])


def card_read(section: dict) -> InlineKeyboardMarkup | None:
    """Уже подтверждённая карточка: только переход назад, без согласия.

    Второй раз согласия не спрашивают — человек его уже дал, и кнопка
    предлагала бы подтвердить то, что подтверждено.
    """
    if section["position"] <= 1:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="← Назад", callback_data=f"{BACK}{section['position'] - 1}"
        )
    ]])


def to_documents() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Перейти к документам", callback_data=DOCS)
    ]])


def document(policy: dict) -> InlineKeyboardMarkup:
    """Кнопки под обязательным документом.

    «Открыть полный документ» стоит первой и отдельной строкой: согласие
    даётся после чтения, а не вместо него.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if policy.get("has_body") or policy.get("has_file"):
        rows.append([InlineKeyboardButton(
            text="Открыть полный документ",
            callback_data=f"{OPEN}{policy['version_id']}",
        )])
    rows.append([
        InlineKeyboardButton(
            text=policy.get("agree_label") or "Согласен",
            callback_data=f"{AGREE}{policy['version_id']}",
        ),
        InlineKeyboardButton(
            text="Не согласен", callback_data=f"{REFUSE}{policy['version_id']}"
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def document_read(policy: dict) -> InlineKeyboardMarkup | None:
    """Подтверждённый документ: только чтение, без повторного согласия."""
    if not (policy.get("has_body") or policy.get("has_file")):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Открыть полный документ",
            callback_data=f"{OPEN}{policy['version_id']}",
        )
    ]])


def documents_list(policies: list[dict]) -> InlineKeyboardMarkup | None:
    """Список документов: по кнопке на каждый.

    Отметка «✓» слева — состояние, а не украшение: по ней видно, что
    осталось, не открывая каждый.
    """
    rows = []
    for one in policies:
        if not one.get("version_id"):
            continue
        mark = "✓ " if one.get("decision") == "ACCEPTED" else ""
        if one.get("decision") == "DECLINED":
            mark = "✗ "
        rows.append([InlineKeyboardButton(
            text=f"{mark}{one['title']}"[:64],
            callback_data=f"{DOC}{one['version_id']}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def finished() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть главное меню", callback_data=MENU)
    ]])


__all__ = [
    "ACK",
    "AGREE",
    "BACK",
    "BTN_CONTINUE",
    "BTN_RULES",
    "DOC",
    "DOCS",
    "LATER",
    "MENU",
    "ONBOARDING_BUTTONS",
    "OPEN",
    "REFUSE",
    "START",
    "card",
    "card_read",
    "document",
    "document_read",
    "documents_list",
    "finished",
    "nudge",
    "to_documents",
    "welcome",
]
