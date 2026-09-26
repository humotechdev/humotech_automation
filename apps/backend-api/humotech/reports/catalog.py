"""Каталог конструктора отчётов: виды, поля и подписи значений.

Один источник на три потребителя: проверку заказа, предпросмотр и файл.
Страница берёт его же через `/reports/catalog` — список полей не
повторяется на клиенте, и чекбокс, которого не умеет сервер, появиться
не может.

Поле — это то, что человек включает галочкой. Колонок у поля может быть
несколько: «Офис и отдел» в файле — две колонки, иначе по ним нельзя
отфильтровать таблицу в Excel.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Виды конструктора. `sessions` и `summary` остаются у старого пути
#: выгрузки, карточек у них нет.
REPORT_KINDS = ("attendance", "worktime", "lateness", "absences", "employees")

KIND_TITLES = {
    "attendance": "Посещаемость",
    "worktime": "Рабочее время",
    "lateness": "Опоздания",
    "absences": "Отсутствия",
    "employees": "Сотрудники",
}

#: Право на сами данные. `reports.export` нужно сверх него всегда.
KIND_PERMISSIONS = {
    "attendance": "attendance.read",
    "worktime": "attendance.read",
    "lateness": "attendance.read",
    "absences": "absences.read",
    "employees": "employees.read",
}

#: Виды, которые идут по дням периода и по офисам.
DAY_KINDS = frozenset({"attendance", "worktime", "lateness"})

#: Типы колонок. От типа зависит, как значение ляжет в файл и как его
#: покажет предпросмотр: секунды в файле — часы числом, на экране —
#: «8 ч 09 м».
COLUMN_TYPES = (
    "text", "number", "date", "time", "hours", "minutes", "status", "bool",
)


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    type: str = "text"


@dataclass(frozen=True)
class Field:
    key: str
    title: str
    columns: tuple[Column, ...]
    #: Включено ли поле, пока человек ничего не трогал.
    default: bool = True


def _one(key: str, title: str, type_: str = "text", *, default: bool = True,
         column: str | None = None) -> Field:
    return Field(key, title, (Column(key, column or title, type_),), default)


_EMPLOYEE = _one("employee", "Сотрудник")
_NUMBER = _one("employee_number", "Табельный номер", default=False)
_OFFICE_DEPARTMENT = Field(
    "office_department", "Офис и отдел",
    (Column("office", "Офис"), Column("department", "Отдел")),
)
_DATE = _one("date", "Дата", "date")

FIELDS: dict[str, tuple[Field, ...]] = {
    "attendance": (
        _EMPLOYEE,
        _NUMBER,
        _OFFICE_DEPARTMENT,
        _DATE,
        _one("schedule", "Рабочий график", default=False),
        _one("first_entry", "Первый вход", "time", column="Вход"),
        _one("last_exit", "Последний выход", "time", column="Выход"),
        _one("marks", "Все входы и выходы", default=False),
        _one("office_time", "Время в офисе", "hours", column="Часы"),
        _one("day_status", "Статус дня", "status", column="Статус"),
    ),
    "worktime": (
        _EMPLOYEE,
        _NUMBER,
        _OFFICE_DEPARTMENT,
        _DATE,
        _one("planned", "Плановое время", "hours", column="План"),
        _one("actual", "Фактическое время", "hours", column="Факт"),
        _one("shortfall", "Недостающие часы", "hours", column="Недостача"),
        _one("overtime", "Переработка", "hours"),
        Field(
            "sessions", "Закрытые и незакрытые сессии",
            (Column("closed_sessions", "Закрытых сессий", "number"),
             Column("open_sessions", "Незакрытых сессий", "number")),
        ),
    ),
    "lateness": (
        _EMPLOYEE,
        _NUMBER,
        _OFFICE_DEPARTMENT,
        _DATE,
        _one("schedule_start", "Начало по графику", "time"),
        _one("first_entry", "Фактический первый вход", "time",
             column="Первый вход"),
        _one("grace", "Допустимое опоздание", "minutes", column="Допуск"),
        _one("late_minutes", "Минуты опоздания", "minutes",
             column="Опоздание"),
        _one("reason", "Причина или заявка на исправление", default=False,
             column="Причина / заявка"),
    ),
    "absences": (
        _EMPLOYEE,
        _NUMBER,
        _OFFICE_DEPARTMENT,
        _one("absence_type", "Тип", column="Тип отсутствия"),
        Field(
            "dates", "Даты",
            (Column("starts", "Начало", "date"),
             Column("ends", "Окончание", "date")),
        ),
        Field(
            "days", "Календарные и рабочие дни",
            (Column("calendar_days", "Календарных дней", "number"),
             Column("working_days", "Рабочих дней", "number")),
        ),
        _one("request_status", "Статус", "status"),
        _one("document", "Наличие документа", column="Документ"),
        _one("decision", "Решение HR", default=False),
    ),
    "employees": (
        _one("employee", "ФИО"),
        _one("employee_number", "Табельный номер"),
        _one("employment_status", "Статус", "status"),
        _one("region", "Регион", default=False),
        _one("office", "Офис"),
        _one("department", "Отдел"),
        _one("position", "Должность"),
        _one("manager", "Руководитель", default=False),
        _one("schedule", "График", default=False),
        _one("hire_date", "Дата приёма", "date"),
        _one("telegram", "Telegram", default=False),
    ),
}

# --- подписи значений ---------------------------------------------------------

#: Статус дня: код → (подпись, тон). Тон нужен только экрану.
DAY_STATUSES = {
    "WORKED": ("Рабочий день", "good"),
    "LATE": ("Опоздание", "warn"),
    "OPEN": ("Сессия не закрыта", "info"),
    "NOT_COME": ("Не пришёл", "bad"),
    "DAY_OFF": ("Выходной", "muted"),
    "NO_SCHEDULE": ("Без графика", "muted"),
    "VACATION": ("Отпуск", "info"),
    "SICK_LEAVE": ("Больничный", "info"),
    "OTHER_ABSENCE": ("Отсутствие", "info"),
}

REQUEST_STATUSES = {
    "DRAFT": ("Черновик", "muted"),
    "SUBMITTED": ("На рассмотрении", "warn"),
    "IN_REVIEW": ("На рассмотрении", "warn"),
    "APPROVED": ("Одобрена", "good"),
    "REJECTED": ("Отклонена", "bad"),
    "CANCELLED": ("Отменена", "muted"),
}

EMPLOYMENT_STATUSES = {
    "ACTIVE": ("Работает", "good"),
    "PROBATION": ("Испытательный срок", "info"),
    "SUSPENDED": ("Приостановлен", "warn"),
    "TERMINATED": ("Уволен", "muted"),
    "ARCHIVED": ("В архиве", "muted"),
}

STATUS_TITLES = {**DAY_STATUSES, **REQUEST_STATUSES, **EMPLOYMENT_STATUSES}

EVENT_TYPES = {"ENTRY": "Вход", "EXIT": "Выход"}
EVENT_SOURCES = {"QR": "QR-код", "MANUAL": "Вручную", "IMPORT": "Импорт"}
VERIFICATION = {"ACCEPTED": "Принята", "REJECTED": "Отклонена",
                "REVIEW": "На проверке"}
TELEGRAM = {"PENDING": "Ожидает привязки", "ACTIVE": "Подключён",
            "REVOKED": "Отключён", "BLOCKED": "Заблокирован"}


def field_keys(kind: str) -> tuple[str, ...]:
    return tuple(field.key for field in FIELDS[kind])


def default_fields(kind: str) -> tuple[str, ...]:
    return tuple(field.key for field in FIELDS[kind] if field.default)


def columns_for(kind: str, fields: tuple[str, ...]) -> list[Column]:
    """Колонки выбранных полей — в порядке каталога, а не выбора.

    Порядок щелчков по галочкам не должен переставлять колонки файла:
    два одинаковых отчёта обязаны выглядеть одинаково.
    """
    chosen = set(fields)
    return [
        column
        for field in FIELDS[kind]
        if field.key in chosen
        for column in field.columns
    ]


__all__ = [
    "COLUMN_TYPES", "Column", "DAY_KINDS", "DAY_STATUSES", "EMPLOYMENT_STATUSES",
    "EVENT_SOURCES", "EVENT_TYPES", "FIELDS", "Field", "KIND_PERMISSIONS",
    "KIND_TITLES", "REPORT_KINDS", "REQUEST_STATUSES", "STATUS_TITLES",
    "TELEGRAM", "VERIFICATION", "columns_for", "default_fields", "field_keys",
]
