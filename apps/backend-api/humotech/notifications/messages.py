"""Тексты уведомлений. Всё, что уходит человеку в Telegram.

Одно место на весь проект. Формулировка уведомления — это то, что человек
увидит в чате рядом с перепиской с родственниками, и собирать её по кусочкам
в трёх сервисах значит однажды отправить туда лишнее.

Чего здесь нет ни в одном тексте:

  * диагноза и причины болезни. Их и в базе-то нет отдельным полем, но
    комментарий сотрудника мог бы содержать что угодно — поэтому он
    в уведомления не попадает вовсе;
  * фамилий других людей. Кто именно из отдела кадров подтвердил заявку —
    сведение служебное, и в чат сотрудника оно не идёт;
  * ссылок с параметрами, идентификаторов и токенов.

Остаётся ровно то, что человеку нужно: что произошло с его заявкой и что
делать дальше.
"""

from __future__ import annotations

from datetime import date

# --- привязка Telegram -----------------------------------------------------

LINK_CONFIRMED = (
    "Доступ открыт. Отдел кадров подтвердил привязку — можно открывать "
    "личный кабинет и отмечаться по QR."
)
LINK_REJECTED = (
    "Отдел кадров не подтвердил привязку этого Telegram. "
    "Если это ошибка, обратитесь в отдел кадров."
)

# --- больничный ------------------------------------------------------------

SICK_CREATED = "Заявка на больничный с {first} по {last} отправлена."
SICK_APPROVED = "Больничный с {first} по {last} подтверждён."
SICK_REJECTED = "Заявка на больничный с {first} по {last} отклонена."
SICK_DOCUMENT_REQUESTED = (
    "К больничному с {first} по {last} нужна справка. "
    "Приложите её в личном кабинете."
)
DOCUMENT_ACCEPTED = "Справка принята. Больше ничего приносить не нужно."
# Причина отклонения приходит от кадровика и уходит человеку дословно:
# пересказывать её своими словами значит однажды смягчить «нечитаемое
# фото» до «нужен другой документ» и получить то же фото второй раз.
DOCUMENT_REJECTED = (
    "Справка не принята.\n\nПричина: {reason}\n\n"
    "Пожалуйста, загрузите корректный документ — в личном кабинете "
    "или прямо в этом чате."
)
DOCUMENT_REJECTED_NO_REASON = (
    "Справка не принята.\n\nПожалуйста, загрузите корректный документ — "
    "в личном кабинете или прямо в этом чате. Причину уточните в отделе "
    "кадров."
)
SICK_EXTENSION_APPROVED = "Больничный продлён по {last}."
SICK_EXTENSION_REJECTED = "Продление больничного по {last} отклонено."

# --- отпуск ----------------------------------------------------------------

VACATION_CREATED = "Заявка на отпуск с {first} по {last} отправлена."
VACATION_APPROVED = "Отпуск с {first} по {last} подтверждён."
VACATION_REJECTED = "Заявка на отпуск с {first} по {last} отклонена."

# --- общее -----------------------------------------------------------------

REQUEST_CANCELLED = "Заявка с {first} по {last} отменена."

# Какой текст брать для какого вида отсутствия. Незнакомый вид получает
# нейтральную формулировку: организация может завести свой, и «отпуск»
# в чате про декрет выглядел бы небрежно.
NEUTRAL_CREATED = "Заявка на отсутствие с {first} по {last} отправлена."
NEUTRAL_APPROVED = "Отсутствие с {first} по {last} подтверждено."
NEUTRAL_REJECTED = "Заявка на отсутствие с {first} по {last} отклонена."

_BY_TYPE = {
    "SICK_LEAVE": {
        "created": SICK_CREATED,
        "approved": SICK_APPROVED,
        "rejected": SICK_REJECTED,
    },
    "ANNUAL_LEAVE": {
        "created": VACATION_CREATED,
        "approved": VACATION_APPROVED,
        "rejected": VACATION_REJECTED,
    },
    "UNPAID_LEAVE": {
        "created": VACATION_CREATED,
        "approved": VACATION_APPROVED,
        "rejected": VACATION_REJECTED,
    },
}


def human_date(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else "—"


def for_request(absence_type_code: str, event: str, first, last) -> str:
    """Текст про заявку. Незнакомый вид — нейтральная формулировка."""
    templates = _BY_TYPE.get(absence_type_code) or {
        "created": NEUTRAL_CREATED,
        "approved": NEUTRAL_APPROVED,
        "rejected": NEUTRAL_REJECTED,
    }
    template = templates.get(event, NEUTRAL_CREATED)
    return template.format(first=human_date(first), last=human_date(last))


# --- трудовой статус -------------------------------------------------------


def _where(facts: dict) -> list[str]:
    """Строки «Офис», «Отдел», «Должность», «График» — те, что заполнены.

    Незаполненное не упоминается вовсе: «График: —» хуже отсутствия
    строки, потому что выглядит как ошибка в системе, а не как
    незаполненное поле.
    """
    rows = []
    for label, key in (
        ("Должность", "position_name"),
        ("Отдел", "department_name"),
        ("Офис", "office_name"),
        ("График", "schedule_name"),
    ):
        if facts.get(key):
            rows.append(f"{label}: {facts[key]}")
    return rows


def hired(facts: dict) -> str:
    """Человека приняли. Первое, что он узнаёт о своём месте работы."""
    name = facts.get("full_name") or "коллега"
    probation = facts.get("employment_status") == "PROBATION"

    head = f"Здравствуйте, {name}!\n\n"
    head += (
        "Вы приняты на стажировку в HUMOTECH."
        if probation
        else "Вы приняты в штат HUMOTECH."
    )
    if facts.get("hire_date"):
        head += f" Дата выхода: {human_date(facts['hire_date'])}."

    rows = _where(facts)
    if rows:
        head += "\n\n" + "\n".join(rows)

    head += "\n\nОтмечаться и подавать заявки можно здесь же, в этом чате."
    return head


def probation_passed(facts: dict) -> str:
    """Стажировка пройдена. Повод сказать это ровно и по делу."""
    name = facts.get("full_name") or "коллега"
    head = f"{name}, поздравляем: вы приняты в штат HUMOTECH."

    rows = _where(facts)
    if rows:
        head += "\n\n" + "\n".join(rows)

    head += "\n\nСпасибо за работу на стажировке. Рады, что вы с нами."
    return head


def probation_ended(last_day: date | None = None) -> str:
    """Расставание после стажировки.

    Без оценок, объяснений и сожалений: решение уже принято, а разговор
    о нём — это разговор с человеком, а не сообщение от бота. Задача
    текста — сообщить факт и сказать, куда обращаться.
    """
    head = "По итогам стажировки мы не продолжаем сотрудничество."
    if last_day:
        head += f"\n\nПоследний рабочий день: {human_date(last_day)}."
    head += (
        "\n\nБлагодарим за время и работу. По вопросам документов и "
        "расчёта обратитесь в отдел кадров."
    )
    return head


__all__ = [
    "DOCUMENT_ACCEPTED",
    "DOCUMENT_REJECTED",
    "DOCUMENT_REJECTED_NO_REASON",
    "LINK_CONFIRMED",
    "LINK_REJECTED",
    "REQUEST_CANCELLED",
    "SICK_DOCUMENT_REQUESTED",
    "SICK_EXTENSION_APPROVED",
    "SICK_EXTENSION_REJECTED",
    "for_request",
    "hired",
    "human_date",
    "probation_ended",
    "probation_passed",
]
