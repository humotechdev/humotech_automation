"""Тексты первичного ознакомления.

Отдельно от хендлера: формулировки правят чаще, чем логику, и держать
их в файле с разбором нажатий — способ задеть второе, правя первое.

Содержимого карточек и документов здесь НЕТ. Оно приходит с сервера —
вместе с подписями кнопок: под правилами распорядка стоит «С правилами
ознакомился», а не «Я ознакомился», и решает это тот, кто пишет текст,
а не тот, кто рисует клавиатуру. В коде бота эти тексты разошлись бы
с CRM на первой же правке кадровика.
"""

from __future__ import annotations

from datetime import datetime

#: Экранирование для HTML-разметки: тексты приходят из CRM, и знак «<»
#: в них — обычный знак, а не начало тега.
_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def escape(raw: str | None) -> str:
    text = raw or ""
    for what, whereto in _ESCAPES:
        text = text.replace(what, whereto)
    return text


def greeting(first_name: str | None) -> str:
    """Первое сообщение после перехода по ссылке ознакомления."""
    name = escape(first_name) or "коллега"
    return (
        f"Добро пожаловать в HUMOTECH, {name}!\n\n"
        "Перед началом работы нужно пройти короткое ознакомление "
        "с компанией и внутренними правилами.\n\n"
        "Это займёт примерно 10–15 минут.\n"
        "Ваш прогресс будет сохранён."
    )


def resume(state: dict) -> str:
    """«Вы остановились на разделе 5 из 10».

    Показывается тому, кто вернулся. Без номера и названия человек не
    понимает, много ли осталось, и закрывает чат второй раз.
    """
    section = state.get("section") or {}
    position = section.get("position")
    title = escape(section.get("title"))
    total = state.get("sections_total") or 0
    if not position:
        return "Продолжим ознакомление."
    return (
        f"Вы остановились на разделе {position} из {total}:\n«{title}»."
    )


def card(section: dict) -> str:
    """Одна информационная карточка."""
    return (
        f"<b>Ознакомление · {section['position']} из {section['total']}</b>\n\n"
        f"<b>{escape(section['title'])}</b>\n\n"
        f"{escape(section['body'])}"
    )


def card_done(section: dict) -> str:
    """Уже пройденная карточка: текст без повторного согласия."""
    when = _moment(section.get("acknowledged_at"))
    mark = "✓ Ознакомление подтверждено"
    if section.get("acknowledged_version"):
        mark += f"\nВерсия {section['acknowledged_version']}"
    if when:
        mark += f" · {when}"
    return f"{card(section)}\n\n{mark}"


def info_done(state: dict) -> str:
    """То же, но числами из ответа сервера.

    Разделов может стать не десять: их правит кадровик в CRM, и
    зашитая десятка однажды начала бы врать.
    """
    total = state.get("sections_total") or 0
    policies = state.get("policies_total") or 0
    return (
        "<b>✓ Ознакомление с компанией завершено</b>\n\n"
        f"Вы прочитали {total} из {total} информационных разделов.\n\n"
        f"Осталось ознакомиться и подтвердить\n"
        f"{policies} {_documents_word(policies)}."
    )


def document(policy: dict) -> str:
    """Экран одного обязательного документа."""
    head = (
        f"<b>Обязательные документы · {policy['index']} из {policy['total']}</b>\n\n"
        f"<b>{escape(policy['title'])}</b>"
    )
    if policy.get("version"):
        head += f"  <i>(версия {escape(policy['version'])})</i>"
    return f"{head}\n\n{escape(policy.get('summary'))}"


def document_done(policy: dict) -> str:
    """Уже подтверждённый документ: без кнопок согласия."""
    when = _moment(policy.get("decided_at"))
    mark = "✓ Согласие подтверждено"
    if policy.get("version"):
        mark += f"\nВерсия {escape(policy['version'])}"
    if when:
        mark += f" · {when}"
    return f"{document(policy)}\n\n{mark}"


DECLINED = (
    "Вы не подтвердили этот документ.\n\n"
    "Отдел кадров получил уведомление и свяжется с вами. "
    "Остальные функции бота работают как обычно."
)

FINISHED = (
    "<b>✓ Первичное ознакомление завершено</b>\n\n"
    "Вы ознакомились с материалами компании\n"
    "и подтвердили обязательные документы.\n\n"
    "Больше напоминать не будем."
)


def nudge(state: dict) -> str:
    """Ненавязчивый блок под меню.

    Одна строка о том, что осталось. Ни предупреждений, ни упоминаний
    о доступе: доступ и так открыт, а незаконченное дело человек и сам
    знает за собой — ему нужно напоминание, а не выговор.
    """
    if state.get("status") == "UPDATE_REQUIRED":
        return (
            "Обновился обязательный документ — нужно подтвердить новую "
            "редакцию."
        )
    if not state.get("info_completed"):
        done = state.get("sections_done", 0)
        total = state.get("sections_total", 0)
        if not done:
            return (
                "Осталось пройти первичное ознакомление — "
                f"{total} коротких разделов, около 10–15 минут."
            )
        return f"Ознакомление не завершено: пройдено {done} из {total} разделов."
    left = state.get("policies_total", 0) - state.get("policies_done", 0)
    return (
        f"Осталось подтвердить обязательные документы: {left} из "
        f"{state.get('policies_total', 0)}."
    )


NOT_ENROLLED = "Ознакомление вам не назначено."

#: Единственное служебное сообщение за всё ознакомление — то, которым
#: устанавливается нижняя клавиатура. Объясняет её появление и главное:
#: прогресс не потеряется.
MENU_INSTALLED = (
    "Ниже — меню бота: отметки, заявки, обращения в HR.\n\n"
    "Ознакомление можно проходить не подряд: прогресс сохраняется, "
    "продолжить получится в любой момент."
)

#: Нажал кнопку до того, как начал. Привязка ждёт согласия, и «вы не
#: связаны с учётной записью» здесь было бы неправдой.
PRESS_START = (
    "Нажмите «Начать ознакомление» в сообщении выше — после этого "
    "откроются разделы."
)


DOCUMENTS_EMPTY = "Обязательных документов пока нет."
FILE_MISSING = "К этому документу файл не приложен."
TEXT_MISSING = "Полный текст этого документа пока не загружен."

#: Ответ на нажатие, которое ничего не меняет. Всплывающая подсказка
#: вместо нового сообщения: чат не должен расти от повторных нажатий.
ALREADY_DONE = "Этот раздел уже подтверждён"
STALE_VERSION = "Документ обновился. Откройте актуальную версию"
ORDER_BROKEN = "Сначала подтвердите предыдущий раздел"
LATER = "Хорошо. Вернуться можно кнопкой «Продолжить ознакомление»"


def _documents_word(count: int) -> str:
    """«документ», «документа», «документов» — по числу."""
    tail = count % 10
    tens = count % 100
    if tens in range(11, 15) or tail == 0 or tail >= 5:
        return "обязательных документов"
    if tail == 1:
        return "обязательный документ"
    return "обязательных документа"


def _moment(raw: str | None) -> str | None:
    """«12.03.2026 09:14» из времени сервера.

    Пояс не пересчитывается: сервер отдаёт момент в своём поясе, и
    гадать о поясе телефона бот не должен — ошибётся молча.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return None


__all__ = [
    "ALREADY_DONE",
    "MENU_INSTALLED",
    "PRESS_START",
    "DECLINED",
    "DOCUMENTS_EMPTY",
    "FILE_MISSING",
    "FINISHED",
    "LATER",
    "NOT_ENROLLED",
    "ORDER_BROKEN",
    "STALE_VERSION",
    "TEXT_MISSING",
    "card",
    "card_done",
    "document",
    "document_done",
    "escape",
    "greeting",
    "info_done",
    "nudge",
    "resume",
]
