"""Ошибки прикладного слоя с устойчивыми кодами.

Коды совпадают с контрактом `packages/api-contracts/telegram-bot.v1.md`:
клиент различает ситуации по `code`, а не по тексту сообщения — текст можно
переводить и переписывать, код нет.

Сюда же переводятся `IntegrityError` из базы. Наружу сырое исключение
SQLAlchemy выходить не должно: в нём текст PostgreSQL, имя ограничения
и фрагмент SQL — для интерфейса это бесполезно, а иногда и небезопасно.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


class DomainError(Exception):
    """Базовая ошибка бизнес-слоя."""

    code = "error"
    http_status = 400

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def as_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          "details": self.details or None}}


class ValidationFailed(DomainError):
    code = "validation_error"
    http_status = 400


class PermissionDenied(DomainError):
    """Нет разрешения либо объект вне области видимости пользователя.

    Намеренно один класс на оба случая: сообщать «объект существует, но он
    не ваш» — значит подтверждать существование чужой записи.
    """

    code = "forbidden"
    http_status = 403


class NotFound(DomainError):
    code = "not_found"
    http_status = 404


class Conflict(DomainError):
    """Состояние объекта не позволяет операцию либо нарушена уникальность."""

    code = "conflict"
    http_status = 409


# Имя ограничения -> понятное человеку объяснение. Список пополняется по мере
# появления новых уникальных ключей; неизвестное ограничение тоже даёт Conflict,
# просто с общим текстом — сырой IntegrityError наружу не уходит никогда.
_CONSTRAINT_MESSAGES: dict[str, str] = {
    "uq_employees_org_number": (
        "Сотрудник с таким табельным номером в организации уже есть"
    ),
    "uq_regions_org_code": "Регион с таким кодом в организации уже есть",
    "uq_offices_org_code": "Офис с таким кодом в организации уже есть",
    "uq_departments_office_code": "Отдел с таким кодом в офисе уже есть",
    "uq_positions_org_code": "Должность с таким кодом в организации уже есть",
    "uq_users_org_lower_email": "Пользователь с таким email уже есть",
    "uq_telegram_accounts_employee": "Telegram уже привязан к этому сотруднику",
    "uq_telegram_accounts_tg_user": "Этот Telegram уже привязан к другому сотруднику",
    "ex_employee_assignments_primary_overlap": (
        "Периоды основных назначений сотрудника пересекаются: "
        "закройте предыдущее назначение перед созданием нового"
    ),
    "ex_employee_schedule_assignments_overlap": (
        "Периоды графиков сотрудника пересекаются: "
        "закройте предыдущий график перед назначением нового"
    ),
    "ck_employees_termination_after_hire": (
        "Дата увольнения не может быть раньше даты приёма"
    ),
    "uq_attendance_sessions_one_open": (
        "У сотрудника уже есть открытая рабочая сессия"
    ),
    "uq_schedule_days_weekday": "В графике уже описан этот день недели",
    "uq_employee_office_access_period": (
        "Доступ к этому офису с той же даты сотруднику уже выдан"
    ),
    "uq_office_networks_cidr": "Такая сеть у офиса уже есть",
    # CHECK-ограничения по этому же участку схемы. Без явного текста они дали бы
    # общее «данные не прошли проверку», а HR должен понимать, что именно не так.
    "ck_employee_assignments_valid_period": (
        "Дата окончания назначения раньше даты начала"
    ),
    "ck_employee_schedule_assignments_valid_period": (
        "Дата окончания действия графика раньше даты начала"
    ),
    "ck_employee_assignments_no_self_manager": (
        "Сотрудник не может быть собственным руководителем"
    ),
    "ck_offices_close_after_open": "Дата закрытия офиса раньше даты открытия",
    "ck_schedule_days_working_day_has_time": (
        "У рабочего дня должны быть указаны время начала и время окончания"
    ),
    "ck_work_schedules_weekly_minutes_positive": (
        "Недельная норма времени должна быть больше нуля"
    ),
    "ck_schedule_days_weekday_range": "День недели задаётся числом от 1 до 7",
}


def constraint_name_of(exc: IntegrityError) -> str | None:
    """Имя нарушенного ограничения из диагностики psycopg."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None)


def translate_integrity_error(exc: IntegrityError) -> DomainError:
    """IntegrityError -> доменная ошибка с устойчивым кодом."""
    name = constraint_name_of(exc)
    message = _CONSTRAINT_MESSAGES.get(name or "")
    if message:
        return Conflict(message, details={"constraint": name})
    if name and name.startswith("ck_"):
        return ValidationFailed(
            "Данные не прошли проверку на уровне базы", details={"constraint": name}
        )
    return Conflict(
        "Операция нарушает ограничение целостности данных",
        details={"constraint": name} if name else {},
    )
