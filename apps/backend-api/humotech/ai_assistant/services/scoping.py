"""Определение области видимости СОТРУДНИКА.

Это НЕ то же самое, что `humotech/core/rbac.py`: тот резолвер работает
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

from django.db.models import Q

from humotech.employees.models import Employee, EmployeeAssignment


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
        """Часть ключа кэша: ответы для разных областей не должны смешиваться.

        Организация входит в отпечаток обязательно. Без неё двое сотрудников
        без действующего назначения (офис и регион «-») из РАЗНЫХ
        организаций при одинаковой ревизии базы знаний получали один ключ,
        и второй видел ответ, собранный по базе знаний первой.
        """
        return (
            f"{self.organization_id}:"
            f"{self.office_id or '-'}:{self.region_id or '-'}:"
            f"{self.department_id or '-'}"
        )


def resolve_employee_scope(
    *, employee_id: uuid.UUID, at: datetime | None = None
) -> EmployeeScope | None:
    """Область сотрудника на момент `at`. None — сотрудник не найден."""
    at = at or datetime.now(tz=timezone.utc)
    day: date = at.date()

    employee = Employee.objects.filter(id=employee_id).first()
    if employee is None:
        return None

    row = (
        EmployeeAssignment.objects.filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=day),
            employee_id=employee_id,
            is_primary=True,
            valid_from__lte=day,
        )
        .order_by("-valid_from")
        .values("office_id", "department_id", "office__region_id")
        .first()
    )

    return EmployeeScope(
        employee_id=employee.id,
        organization_id=employee.organization_id,
        office_id=row["office_id"] if row else None,
        region_id=row["office__region_id"] if row else None,
        department_id=row["department_id"] if row else None,
        language=employee.preferred_language or "ru",
        employment_status=employee.employment_status,
    )
