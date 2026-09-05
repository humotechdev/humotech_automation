"""Тексты сотруднического меню и превращение ответов API в сообщения.

Форматирование живёт здесь, а не в хендлерах: одно число — «часы за
неделю» — показывается в трёх местах, и складывать его в трёх местах
значит однажды сложить по-разному.

Считает всё backend. Здесь только перевод его ответа на человеческий:
секунды в часы, коды состояний в слова, даты в привычный вид.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Из стандартной библиотеки, а не из `zoneinfo`: базы часовых поясов
# в системе может не быть вовсе (Windows), и падать на импорте модуля
# с текстами из-за этого нельзя. Пояс офиса всё равно приходит по имени,
# и за него отвечает `tzdata` в зависимостях.
UTC = dt_timezone.utc

# --- состояния доступа -----------------------------------------------------

NOT_LINKED = (
    "Этот Telegram не связан с учётной записью сотрудника.\n\n"
    "Попросите персональную ссылку в отделе кадров — она открывается один раз."
)
PENDING = (
    "Вы перешли по ссылке, и заявка ушла в отдел кадров.\n\n"
    "Доступ появится после подтверждения. Открывать что-то заново не нужно — "
    "просто нажмите кнопку ещё раз позже."
)
NO_ACCESS = (
    "Доступ к личному кабинету закрыт.\n\n"
    "Если это ошибка, обратитесь в отдел кадров."
)
BACKEND_DOWN = (
    "Сервис временно недоступен. Попробуйте через пару минут — "
    "с вашими данными ничего не случилось."
)

# --- состояния присутствия -------------------------------------------------

PRESENCE = {
    "IN_OFFICE": "Вы в офисе",
    "OUTSIDE": "Вы вне офиса",
    "SICK_LEAVE": "У вас больничный",
    "VACATION": "У вас отпуск",
    "OTHER_ABSENCE": "У вас оформлено отсутствие",
    "DAY_OFF": "Сегодня выходной",
    "WORKDAY_MISSED": "Рабочий день закончился, отметок нет",
}

# --- статусы заявок --------------------------------------------------------

REQUEST_STATUS = {
    "DRAFT": "черновик",
    "SUBMITTED": "ожидает решения",
    "IN_REVIEW": "на рассмотрении",
    "APPROVED": "подтверждена",
    "REJECTED": "отклонена",
    "CANCELLED": "отменена",
}

HELP = (
    "<b>Что умеет бот</b>\n\n"
    "📍 <b>Я сейчас в офисе?</b> — текущее состояние: в офисе, вне офиса, "
    "больничный, отпуск или выходной.\n"
    "📅 <b>Сегодня / За неделю / За месяц</b> — сколько времени вы провели "
    "в офисе.\n"
    "🕘 <b>История посещений</b> — входы и выходы по дням.\n"
    "🤒 <b>Больничный</b> и 🏖 <b>Отпуск</b> — оформляются в личном кабинете: "
    "там есть календарь и подсказки.\n"
    "📄 <b>Мои заявки</b> — что подано и что с ним стало.\n\n"
    "<b>Как отметиться</b>\n"
    "Откройте личный кабинет и наведите камеру на экран у входа. "
    "Вход это или выход, определяет сервер — выбирать ничего не нужно.\n\n"
    "<b>Если что-то не сходится</b>\n"
    "Отметка не прошла, время неверное, заявка потерялась — это к отделу "
    "кадров. Бот показывает данные, но не исправляет их."
)

CABINET_HINT = (
    "Синяя кнопка «Отметиться» у поля ввода — приход и уход по QR. "
    "Одно нажатие, камера, готово.\n"
    "Команда /cabinet — личный кабинет: статистика, история, "
    "больничный и отпуск."
)

CABINET_OPEN = (
    "Нажмите кнопку ниже — кабинет откроется внутри Telegram.\n\n"
    "Там ваш статус, часы, история, больничный и отпуск. "
    "Отметиться быстрее синей кнопкой «Отметиться» у поля ввода."
)

CABINET_UNAVAILABLE = (
    "Кабинет пока не настроен: администратор не указал его адрес. "
    "Всё остальное в меню работает."
)


def greet(profile: dict) -> str:
    employee = profile.get("employee") or {}
    office = profile.get("office") or {}
    position = profile.get("position") or {}
    lines = [f"<b>{employee.get('full_name', 'Сотрудник')}</b>"]
    if position.get("name"):
        lines.append(position["name"])
    if office.get("name"):
        lines.append(f"Офис: {office['name']}")
    return "\n".join(lines)


def presence(status: dict) -> str:
    """Короткий ответ на «я сейчас в офисе?».

    Открытая сессия и «часы сегодня» показываются раздельно: в три часа
    ночи у зашедшего в 22:00 сегодняшних часов честно ноль, а в офисе он
    пять часов. Сводить это в одно число значит соврать в одном из мест.
    """
    tz = _zone(status.get("timezone"))
    lines = [f"<b>{PRESENCE.get(status['state'], 'Состояние неизвестно')}</b>"]

    session = status.get("open_session")
    if session:
        lines.append(
            f"С {_time(session['started_at'], tz)} — "
            f"уже {duration(session['seconds'])}."
        )
        if session.get("day") != status.get("day"):
            lines.append("Смена началась вчера и ещё не закрыта.")
    if status.get("absence_name") and status["state"] in (
        "SICK_LEAVE", "VACATION", "OTHER_ABSENCE"
    ):
        lines.append(status["absence_name"])

    lines.append(f"Сегодня в офисе: {duration(status.get('seconds_today', 0))}")

    if status.get("scheduled_start") and status.get("scheduled_end"):
        lines.append(
            f"График на сегодня: {status['scheduled_start'][:5]}–"
            f"{status['scheduled_end'][:5]}"
        )
    if status.get("last_entry_at"):
        lines.append(f"Последний вход: {_moment(status['last_entry_at'], tz)}")
    if status.get("last_exit_at"):
        lines.append(f"Последний выход: {_moment(status['last_exit_at'], tz)}")
    return "\n".join(lines)


def summary(body: dict, title: str) -> str:
    """Итог за период. Числа берутся как есть — считал их backend."""
    data = body["summary"]
    lines = [
        f"<b>{title}</b>",
        f"{_period(data)}",
        "",
        f"В офисе: {duration(data['seconds'])}",
        f"Завершённых сессий: {data['completed_sessions']}",
    ]
    if data["open_sessions"]:
        lines.append(
            f"Открытых сессий: {data['open_sessions']} — время предварительное"
        )
    lines.append(f"Дней с отметками: {data['attended_days']}")

    if data["has_schedule"]:
        lines.append(f"Рабочих дней: {data['working_days']}")
        lines.append(f"Пропущено: {data['missed_days']}")
    else:
        # Не ноль, а «неизвестно»: ноль рабочих дней при нуле пропусков
        # читался бы как безупречная посещаемость.
        lines.append("Рабочих дней: график не назначен")

    if data["sick_leave_days"]:
        lines.append(f"Больничный: {data['sick_leave_days']} дн.")
    if data["vacation_days"]:
        lines.append(f"Отпуск: {data['vacation_days']} дн.")
    if data["other_absence_days"]:
        lines.append(f"Прочие отсутствия: {data['other_absence_days']} дн.")
    return "\n".join(lines)


def history(body: dict) -> str:
    days = body.get("days") or []
    if not days:
        return "За этот период отметок нет."

    tz = _zone((body.get("period") or {}).get("timezone"))
    lines = ["<b>История посещений</b>", ""]
    for day in days:
        lines.append(f"<b>{_date(day['day'])}</b> — {duration(day['seconds'])}")
        for session in day.get("sessions", []):
            entry = _time(session["started_at"], tz)
            if session["is_open"]:
                lines.append(f"  вход {entry} — ещё в офисе")
            else:
                lines.append(f"  {entry} — {_time(session['ended_at'], tz)}")
            where = session.get("office_name")
            point = session.get("entry_point_name")
            if where and point:
                lines.append(f"  {where}, {point}")
        if day.get("absence_name"):
            lines.append(f"  {day['absence_name']}")
        lines.append("")

    if body.get("has_more"):
        lines.append("Показаны последние дни. Полная история — в кабинете.")
    return "\n".join(lines).strip()


def requests(body: dict) -> str:
    rows = body.get("requests") or []
    if not rows:
        return (
            "Заявок пока нет.\n\n"
            "Оформить больничный или отпуск можно в личном кабинете."
        )

    lines = ["<b>Мои заявки</b>", ""]
    for row in rows:
        kind = row["absence_type"]["name"]
        status = REQUEST_STATUS.get(row["status"], row["status"].lower())
        lines.append(
            f"<b>{kind}</b>: {_date(row['first_day'])} — {_date(row['last_day'])}"
        )
        lines.append(f"  {status}, рабочих дней: {row['working_days']}")
        if row.get("extension_pending"):
            lines.append("  продление ждёт решения")
        if row.get("review_comment"):
            lines.append(f"  комментарий: {row['review_comment']}")
        lines.append("")
    return "\n".join(lines).strip()


def balance_line(body: dict) -> str:
    rows = body.get("balances") or []
    if not rows:
        return ""
    parts = [
        f"{row['absence_type']['name']}: {row['available_days']:g} дн."
        for row in rows
    ]
    return "Остаток: " + "; ".join(parts)


# --- перевод чисел и дат ---------------------------------------------------

def duration(seconds: int) -> str:
    """Секунды в «8 ч 30 мин».

    Округление живёт здесь и только здесь: сервер отдаёт секунды, потому
    что округливший однажды теряет разницу навсегда.
    """
    if not seconds:
        return "0 мин"
    hours, rest = divmod(int(seconds), 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


def _period(data: dict) -> str:
    if data["first"] == data["last"]:
        return _date(data["first"])
    return f"{_date(data['first'])} — {_date(data['last'])}"


def _date(value: str | None) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).strftime("%d.%m.%Y")


def _zone(name: str | None):
    """Пояс офиса, который backend прислал вместе с данными.

    Без него всё показывалось бы в UTC: сервер отдаёт моменты времени
    со смещением +00:00, и `strftime` честно напечатал бы их как есть —
    минус пять часов для душанбинского офиса.
    """
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _time(value: str | None, tz=UTC) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(tz).strftime("%H:%M")


def _moment(value: str | None, tz=UTC) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(tz).strftime("%d.%m %H:%M")


__all__ = [
    "BACKEND_DOWN",
    "CABINET_HINT",
    "CABINET_OPEN",
    "CABINET_UNAVAILABLE",
    "HELP",
    "NOT_LINKED",
    "NO_ACCESS",
    "PENDING",
    "PRESENCE",
    "balance_line",
    "duration",
    "greet",
    "history",
    "presence",
    "requests",
    "summary",
]
