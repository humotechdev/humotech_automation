"""Тексты привязки по одноразовой ссылке.

Отдельно от хендлера: формулировки правят чаще, чем логику, и держать их
в файле с разбором полезной нагрузки — способ задеть второе, правя первое.
"""

from __future__ import annotations

LINK_PENDING = (
    "Добро пожаловать в HUMOTECH!\n\n"
    "Перед началом работы ознакомьтесь с правилами: используйте личный "
    "аккаунт Telegram, не передавайте ссылку другим людям и не публикуйте "
    "рабочие данные в общих чатах.\n\n"
    "Нажимая кнопку ниже, вы принимаете условия использования бота."
)
LINK_CONNECTED = "Готово! Telegram подключён. Теперь доступны отметки, заявки и обращение в HR."
LINK_EXPIRED = "Срок действия ссылки истёк. Попросите новую в отделе кадров."
LINK_REVOKED = "Ссылка отозвана. Попросите новую в отделе кадров."
LINK_USED = "Эта ссылка уже использована."
LINK_AWAITING = "Ссылка уже использована, привязка ждёт подтверждения."
LINK_INVALID = "Ссылка недействительна. Попросите новую в отделе кадров."
LINK_TAKEN = "Этот Telegram уже привязан к другому сотруднику."
LINK_INACTIVE = "Привязка недоступна. Обратитесь в отдел кадров."
LINK_ERROR = "Не получилось. Попробуйте ещё раз через пару минут."

# Код причины из ответа backend -> что сказать человеку. Причина приходит
# в `error.details.reason`; текст ответа не разбирается — он переводится
# и переписывается, а код нет.
LINK_MESSAGES = {
    "expired": LINK_EXPIRED,
    "revoked": LINK_REVOKED,
    "used": LINK_USED,
    "pending": LINK_AWAITING,
    "invalid": LINK_INVALID,
    "telegram_taken": LINK_TAKEN,
    "employee_inactive": LINK_INACTIVE,
}


#: Трудовой статус глазами человека. «PROBATION» ему ничего не говорит.
EMPLOYMENT_WORDS = {
    "PROBATION": "на стажировку",
    "ACTIVE": "в штат",
}


def welcome(facts: dict) -> str:
    """Первое сообщение узнанному сотруднику.

    Здоровается по имени и называет то, что человек и так про себя знает:
    куда принят, в какой офис, по какому графику и к кому обращаться.
    Незаполненные поля просто не упоминаются — строка «График: —» хуже
    отсутствия строки.
    """
    name = facts.get("full_name") or "коллега"
    where = EMPLOYMENT_WORDS.get(facts.get("employment_status") or "", "на работу")
    since = facts.get("hire_date")

    head = f"Добро пожаловать в HUMOTECH, {name}."
    head += f"\n\nВы приняты {where}"
    if since:
        head += f" с {since}"
    head += "."

    rows = []
    if facts.get("office_name"):
        rows.append(f"Офис: {facts['office_name']}")
    if facts.get("department_name"):
        rows.append(f"Отдел: {facts['department_name']}")
    if facts.get("position_name"):
        rows.append(f"Должность: {facts['position_name']}")
    if facts.get("schedule_name"):
        rows.append(f"График: {facts['schedule_name']}")
    if facts.get("manager_name"):
        rows.append(f"Руководитель: {facts['manager_name']}")

    if rows:
        head += "\n\n" + "\n".join(rows)

    head += (
        "\n\nПожалуйста, ознакомьтесь с правилами компании и подтвердите "
        "согласие — кнопка ниже.\n\n"
        "Доступ к отметкам и заявкам появится, когда отдел кадров "
        "подтвердит привязку."
    )
    return head


#: Почему узнать не вышло. Коды те же, что у ссылки, плюс свои.
RECOGNIZE_MESSAGES = {
    "no_username": (
        "У вашего Telegram нет имени пользователя, поэтому узнать вас "
        "не получилось.\n\nПопросите персональную ссылку в отделе кадров."
    ),
    "unknown": (
        "Мы вас пока не ждём.\n\nЕсли вы только устроились — попросите "
        "персональную ссылку в отделе кадров."
    ),
    "ambiguous": (
        "Ваше имя в Telegram указано у нескольких сотрудников. "
        "Обратитесь в отдел кадров."
    ),
}
