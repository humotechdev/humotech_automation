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


__all__ = [
    "LINK_CONFIRMED",
    "LINK_REJECTED",
    "REQUEST_CANCELLED",
    "SICK_DOCUMENT_REQUESTED",
    "SICK_EXTENSION_APPROVED",
    "SICK_EXTENSION_REJECTED",
    "for_request",
    "human_date",
]
