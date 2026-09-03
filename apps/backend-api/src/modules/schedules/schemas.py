"""Контракт рабочих графиков и их назначения сотрудникам."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict


class ScheduleBreakSpec(BaseModel):
    name: str
    start_time: time
    end_time: time
    is_paid: bool = False


class ScheduleDaySpec(BaseModel):
    """День недели по ISO-8601: 1 — понедельник, 7 — воскресенье."""

    weekday: int
    is_working_day: bool
    start_time: time | None = None
    end_time: time | None = None
    crosses_midnight: bool = False
    breaks: list[ScheduleBreakSpec] = []


class ScheduleBreakView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    start_time: time
    end_time: time
    is_paid: bool


class ScheduleDayView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    weekday: int
    is_working_day: bool
    start_time: time | None
    end_time: time | None
    crosses_midnight: bool
    breaks: list[ScheduleBreakView] = []


class WorkScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    timezone: str
    weekly_minutes: int
    late_grace_minutes: int
    early_leave_grace_minutes: int
    is_flexible: bool
    status: str
    created_at: datetime
    updated_at: datetime


class WorkScheduleDetail(WorkScheduleView):
    days: list[ScheduleDayView] = []


class WorkScheduleCreateRequest(BaseModel):
    name: str
    timezone: str
    weekly_minutes: int
    late_grace_minutes: int = 0
    early_leave_grace_minutes: int = 0
    is_flexible: bool = False
    days: list[ScheduleDaySpec] = []


class WorkScheduleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    timezone: str | None = None
    weekly_minutes: int | None = None
    late_grace_minutes: int | None = None
    early_leave_grace_minutes: int | None = None
    is_flexible: bool | None = None
    # переданный список ДНЕЙ заменяет расписание целиком: частичная правка
    # отдельных дней ввела бы неочевидную семантику «слияния»
    days: list[ScheduleDaySpec] | None = None


class ScheduleAssignmentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    schedule_id: uuid.UUID
    valid_from: date
    valid_to: date | None
    assigned_by_user_id: uuid.UUID | None
    created_at: datetime
