"""Сотрудник, история его назначений и доступ к дополнительным офисам."""

from __future__ import annotations

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.db import models
from django.db.models import F, Func, Q, Value

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    EMPLOYMENT_STATUSES,
    EMPLOYMENT_TYPES,
    OFFICE_ACCESS_TYPES,
    WORK_MODES,
    choices,
    status_check,
)
from humotech.core.models import (
    ArchivableModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


def validity_daterange() -> Func:
    """`daterange(valid_from, valid_to, '[]')` — период с ВКЛЮЧИТЕЛЬНЫМИ границами.

    Именно `'[]'`, а не значение по умолчанию `'[)'`: день окончания входит
    в период. Отсюда следует правило, на которое опирается весь перенос
    сотрудника: закрывать предыдущий период надо датой на день РАНЬШЕ начала
    нового, иначе общий день считается пересечением и строка не вставится.
    """
    return Func(
        F("valid_from"), F("valid_to"), Value("[]"),
        function="daterange",
        output_field=DateRangeField(),
    )


class Employee(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    """Сотрудник никогда не удаляется физически: у него есть история отметок."""

    employee_number = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    corporate_email = models.CharField(max_length=255, null=True, blank=True)
    personal_email = models.CharField(max_length=255, null=True, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    hire_date = models.DateField()
    termination_date = models.DateField(null=True, blank=True)
    preferred_language = models.CharField(max_length=10, db_default="ru")
    employment_status = models.CharField(
        max_length=30, choices=choices(EMPLOYMENT_STATUSES)
    )
    # денормализованный флаг для быстрых выборок; источник правды — telegram_accounts
    telegram_connected = models.BooleanField(db_default=False)

    class Meta:
        db_table = "employees"
        verbose_name = "сотрудник"
        verbose_name_plural = "сотрудники"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "employee_number"],
                name="uq_employees_org_number",
            ),
            status_check(
                "employment_status", EMPLOYMENT_STATUSES,
                "ck_employees_employment_status",
            ),
            raw_check(
                "termination_date IS NULL OR termination_date >= hire_date",
                "ck_employees_termination_after_hire",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_employees_organization_id"),
            models.Index(
                fields=["organization", "employment_status"],
                name="ix_employees_org_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_number} {self.last_name} {self.first_name}"


class EmployeeAssignment(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """История переводов: офис, отдел, должность, руководитель на период.

    Текущее назначение — то, у которого valid_to IS NULL либо период включает
    сегодняшнюю дату. Основное назначение (is_primary) определяет офис сотрудника
    по умолчанию для QR-отметки.
    """

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="assignments",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        related_name="assignments",
    )
    department = models.ForeignKey(
        "departments.Department",
        on_delete=models.PROTECT,
        db_column="department_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="assignments",
    )
    position = models.ForeignKey(
        "positions.Position",
        on_delete=models.PROTECT,
        db_column="position_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="assignments",
    )
    manager_employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        db_column="manager_employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="subordinate_assignments",
    )
    employment_type = models.CharField(
        max_length=30, choices=choices(EMPLOYMENT_TYPES)
    )
    work_mode = models.CharField(max_length=30, choices=choices(WORK_MODES))
    is_primary = models.BooleanField(db_default=True)
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "employee_assignments"
        verbose_name = "назначение сотрудника"
        verbose_name_plural = "назначения сотрудников"
        constraints = [
            status_check(
                "employment_type", EMPLOYMENT_TYPES,
                "ck_employee_assignments_employment_type",
            ),
            status_check(
                "work_mode", WORK_MODES, "ck_employee_assignments_work_mode"
            ),
            raw_check(
                "valid_to IS NULL OR valid_to >= valid_from",
                "ck_employee_assignments_valid_period",
            ),
            raw_check(
                "manager_employee_id IS NULL OR manager_employee_id <> employee_id",
                "ck_employee_assignments_no_self_manager",
            ),
            # У сотрудника не может быть двух пересекающихся ОСНОВНЫХ назначений.
            # Требует расширения btree_gist: без него gist-индекс не примет
            # колонку uuid рядом с диапазоном.
            ExclusionConstraint(
                name="ex_employee_assignments_primary_overlap",
                expressions=[
                    ("employee_id", RangeOperators.EQUAL),
                    (validity_daterange(), RangeOperators.OVERLAPS),
                ],
                condition=Q(is_primary=True),
                index_type="GIST",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_employee_assignments_organization_id"
            ),
            models.Index(
                fields=["employee"], name="ix_employee_assignments_employee_id"
            ),
            models.Index(fields=["office"], name="ix_employee_assignments_office_id"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} @ {self.office_id} с {self.valid_from}"


class EmployeeOfficeAccess(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Право отмечаться в дополнительном офисе, помимо основного назначения."""

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="office_access",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        related_name="extra_access",
    )
    access_type = models.CharField(
        max_length=30, choices=choices(OFFICE_ACCESS_TYPES)
    )
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    # админа могут удалить — история доступа от этого исчезать не должна
    granted_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="granted_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "employee_office_access"
        verbose_name = "доступ к офису"
        verbose_name_plural = "доступы к офисам"
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "office", "valid_from"],
                name="uq_employee_office_access_period",
            ),
            status_check(
                "access_type", OFFICE_ACCESS_TYPES,
                "ck_employee_office_access_access_type",
            ),
            raw_check(
                "valid_to IS NULL OR valid_to >= valid_from",
                "ck_employee_office_access_valid_period",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"],
                name="ix_employee_office_access_organization_id",
            ),
            models.Index(
                fields=["employee"], name="ix_employee_office_access_employee_id"
            ),
            models.Index(
                fields=["office"], name="ix_employee_office_access_office_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} -> {self.office_id}"
