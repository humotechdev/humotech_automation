"""Определение области видимости СОТРУДНИКА.

Это НЕ то же самое, что `core/permissions/scopes.py`: тот резолвер работает
от `user_id` — учётной записи CRM. У сотрудника, который пишет боту, строки
в `users` может не быть вообще. Перепутать эти два резолвера опасно: изоляция
офисов при этом будет выглядеть работающей и молча пропускать чужие правила.

Область сотрудника выводится из его основного назначения:
    employee -> действующее primary-назначение -> office -> region
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.employees.models import Employee, EmployeeAssignment
from src.modules.offices.models import Office


@dataclass(frozen=True)
class EmployeeScope:
    employee_id: uuid.UUID
    organization_id: uuid.UUID
    office_id: uuid.UUID | None
    region_id: uuid.UUID | None
    department_id: uuid.UUID | None
    language: str
    employment_status: str

    @property
    def is_active(self) -> bool:
        return self.employment_status in ("ACTIVE", "PROBATION")

    def cache_fingerprint(self) -> str:
        """Часть ключа кэша: ответы для разных областей не должны смешиваться."""
        return f"{self.office_id or '-'}:{self.region_id or '-'}:{self.department_id or '-'}"


def resolve_employee_scope(
    session: Session,
    *,
    employee_id: uuid.UUID,
    at: datetime | None = None,
) -> EmployeeScope | None:
    """Область сотрудника на момент `at`. None — сотрудник не найден."""
    at = at or datetime.now(tz=timezone.utc)
    day: date = at.date()

    employee = session.get(Employee, employee_id)
    if employee is None:
        return None

    row = session.execute(
        select(EmployeeAssignment.office_id, EmployeeAssignment.department_id,
               Office.region_id)
        .join(Office, Office.id == EmployeeAssignment.office_id)
        .where(
            EmployeeAssignment.employee_id == employee_id,
            EmployeeAssignment.is_primary.is_(True),
            EmployeeAssignment.valid_from <= day,
            (EmployeeAssignment.valid_to.is_(None))
            | (EmployeeAssignment.valid_to >= day),
        )
        .order_by(EmployeeAssignment.valid_from.desc())
        .limit(1)
    ).first()

    office_id = row[0] if row else None
    department_id = row[1] if row else None
    region_id = row[2] if row else None

    return EmployeeScope(
        employee_id=employee.id,
        organization_id=employee.organization_id,
        office_id=office_id,
        region_id=region_id,
        department_id=department_id,
        language=employee.preferred_language or "ru",
        employment_status=employee.employment_status,
    )
