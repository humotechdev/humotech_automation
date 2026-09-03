"""Контракт карточки сотрудника.

Карточка собирается из нескольких таблиц (сотрудник, назначение, офис, регион,
отдел, должность, график, Telegram), поэтому её форма описана явно. Отдавать
ORM-объект наружу нельзя: половина связей ленивая, и каждый доступ к полю
превращался бы в отдельный запрос уже за пределами сервиса.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class TelegramBindingView(BaseModel):
    """Состояние привязки Telegram. Сам идентификатор наружу не отдаётся:
    для CRM важен факт привязки, а не номер аккаунта."""

    connected: bool
    status: str | None = None
    username: str | None = None
    connected_at: datetime | None = None


class AssignmentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    office_id: uuid.UUID
    office_code: str | None = None
    office_name: str | None = None
    office_timezone: str | None = None
    region_id: uuid.UUID | None = None
    region_code: str | None = None
    region_name: str | None = None
    department_id: uuid.UUID | None = None
    department_name: str | None = None
    position_id: uuid.UUID | None = None
    position_name: str | None = None
    manager_employee_id: uuid.UUID | None = None
    employment_type: str
    work_mode: str
    is_primary: bool
    valid_from: date
    valid_to: date | None


class ScheduleBriefView(BaseModel):
    schedule_id: uuid.UUID
    name: str
    timezone: str
    status: str
    valid_from: date
    valid_to: date | None


class EmployeeListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    employee_number: str
    first_name: str
    last_name: str
    middle_name: str | None
    full_name: str = ""
    phone: str | None
    corporate_email: str | None
    employment_status: str
    hire_date: date
    termination_date: date | None
    telegram_connected: bool
    created_at: datetime
    # текущее основное назначение — плоско, чтобы список читался без доп. запросов
    office_id: uuid.UUID | None = None
    office_name: str | None = None
    region_id: uuid.UUID | None = None
    region_name: str | None = None
    department_name: str | None = None
    position_name: str | None = None


class EmployeeCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    employee_number: str
    first_name: str
    last_name: str
    middle_name: str | None
    full_name: str = ""
    phone: str | None
    corporate_email: str | None
    personal_email: str | None
    birth_date: date | None
    hire_date: date
    termination_date: date | None
    preferred_language: str
    employment_status: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime

    current_assignment: AssignmentView | None = None
    current_schedule: ScheduleBriefView | None = None
    telegram: TelegramBindingView = TelegramBindingView(connected=False)
    assignment_history: list[AssignmentView] = []


class EmployeeCreateRequest(BaseModel):
    employee_number: str
    first_name: str
    last_name: str
    middle_name: str | None = None
    hire_date: date
    office_id: uuid.UUID
    employment_type: str = "FULL_TIME"
    work_mode: str = "ONSITE"
    # регион можно не передавать: он определяется офисом. Если передан —
    # обязан совпасть с регионом офиса, иначе это ошибка в данных вызывающего.
    region_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    position_id: uuid.UUID | None = None
    manager_employee_id: uuid.UUID | None = None
    phone: str | None = None
    corporate_email: str | None = None
    personal_email: str | None = None
    birth_date: date | None = None
    preferred_language: str = "ru"
    employment_status: str = "ACTIVE"


class EmployeeUpdateRequest(BaseModel):
    """Только собственные данные сотрудника.

    Офис, отдел, должность и график живут в назначениях и меняются отдельными
    операциями: у них есть период действия, а у поля карточки его нет.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    phone: str | None = None
    corporate_email: str | None = None
    personal_email: str | None = None
    birth_date: date | None = None
    preferred_language: str | None = None
    employee_number: str | None = None


class AssignmentChangeRequest(BaseModel):
    """Перевод: новый офис, отдел, должность или руководитель с указанной даты."""

    effective_from: date
    office_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    position_id: uuid.UUID | None = None
    manager_employee_id: uuid.UUID | None = None
    employment_type: str | None = None
    work_mode: str | None = None
