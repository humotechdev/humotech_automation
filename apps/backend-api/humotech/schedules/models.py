"""Рабочие графики, дни недели, перерывы, назначение графика и календарь."""

from __future__ import annotations

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    CALENDAR_EXCEPTION_TYPES,
    SCHEDULE_STATUSES,
    choices,
    status_check,
)
from humotech.core.models import (
    ArchivableModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)
from humotech.employees.models import validity_daterange


class WorkSchedule(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    name = models.CharField(max_length=255)
    timezone = models.CharField(max_length=100)
    weekly_minutes = models.IntegerField()
    late_grace_minutes = models.IntegerField(db_default=0)
    early_leave_grace_minutes = models.IntegerField(db_default=0)
    is_flexible = models.BooleanField(db_default=False)
    status = models.CharField(max_length=30, choices=choices(SCHEDULE_STATUSES))

    class Meta:
        db_table = "work_schedules"
        verbose_name = "рабочий график"
        verbose_name_plural = "рабочие графики"
        constraints = [
            status_check("status", SCHEDULE_STATUSES, "ck_work_schedules_status"),
            raw_check(
                "weekly_minutes > 0", "ck_work_schedules_weekly_minutes_positive"
            ),
            raw_check(
                "late_grace_minutes >= 0",
                "ck_work_schedules_late_grace_non_negative",
            ),
            raw_check(
                "early_leave_grace_minutes >= 0",
                "ck_work_schedules_early_grace_non_negative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_work_schedules_organization_id"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ScheduleDay(UUIDPrimaryKeyModel, TimestampedModel):
    """День недели в графике. 1 = понедельник, 7 = воскресенье (ISO-8601)."""

    schedule = models.ForeignKey(
        WorkSchedule,
        on_delete=models.CASCADE,
        db_column="schedule_id",
        db_index=False,
        related_name="days",
    )
    weekday = models.SmallIntegerField()
    is_working_day = models.BooleanField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    # ночная смена: конец приходится на следующие сутки
    crosses_midnight = models.BooleanField(db_default=False)

    class Meta:
        db_table = "schedule_days"
        verbose_name = "день графика"
        verbose_name_plural = "дни графика"
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "weekday"], name="uq_schedule_days_weekday"
            ),
            raw_check("weekday >= 1 AND weekday <= 7", "ck_schedule_days_weekday_range"),
            # У рабочего дня время начала и конца обязательно. Отличить ночную
            # смену от опечатки одним CHECK нельзя — это делает прикладной слой.
            raw_check(
                "NOT is_working_day OR (start_time IS NOT NULL AND end_time IS NOT NULL)",
                "ck_schedule_days_working_day_has_time",
            ),
        ]
        indexes = [
            models.Index(fields=["schedule"], name="ix_schedule_days_schedule_id"),
        ]

    def __str__(self) -> str:
        return f"день {self.weekday}"


class ScheduleBreak(UUIDPrimaryKeyModel, TimestampedModel):
    schedule_day = models.ForeignKey(
        ScheduleDay,
        on_delete=models.CASCADE,
        db_column="schedule_day_id",
        db_index=False,
        related_name="breaks",
    )
    name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_paid = models.BooleanField(db_default=False)

    class Meta:
        db_table = "schedule_breaks"
        verbose_name = "перерыв"
        verbose_name_plural = "перерывы"
        indexes = [
            models.Index(
                fields=["schedule_day"], name="ix_schedule_breaks_schedule_day_id"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class EmployeeScheduleAssignment(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Какой график действует у сотрудника и с какой даты."""

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="schedule_assignments",
    )
    schedule = models.ForeignKey(
        WorkSchedule,
        on_delete=models.PROTECT,
        db_column="schedule_id",
        db_index=False,
        related_name="employee_assignments",
    )
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    assigned_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="assigned_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "employee_schedule_assignments"
        verbose_name = "назначение графика"
        verbose_name_plural = "назначения графиков"
        constraints = [
            raw_check(
                "valid_to IS NULL OR valid_to >= valid_from",
                "ck_employee_schedule_assignments_valid_period",
            ),
            # У сотрудника не может быть двух пересекающихся графиков.
            ExclusionConstraint(
                name="ex_employee_schedule_assignments_overlap",
                expressions=[
                    ("employee_id", RangeOperators.EQUAL),
                    (validity_daterange(), RangeOperators.OVERLAPS),
                ],
                index_type="GIST",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"],
                name="ix_employee_schedule_assignments_organization_id",
            ),
            models.Index(
                fields=["employee"],
                name="ix_employee_schedule_assignments_employee_id",
            ),
            models.Index(
                fields=["schedule"],
                name="ix_employee_schedule_assignments_schedule_id",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} -> {self.schedule_id} с {self.valid_from}"


class CalendarException(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Праздники, сокращённые дни, рабочие выходные.

    office_id NULL — исключение действует на всю организацию.
    """

    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="calendar_exceptions",
    )
    date = models.DateField()
    name = models.CharField(max_length=255)
    exception_type = models.CharField(
        max_length=30, choices=choices(CALENDAR_EXCEPTION_TYPES)
    )
    is_working_day = models.BooleanField()
    #: Основание переноса. Отдельно от `name`: название видит сотрудник
    #: в календаре («Навруз»), а основание читает кадровик, разбирая,
    #: почему суббота стала рабочей («приказ №14 от 03.03»).
    reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "calendar_exceptions"
        verbose_name = "исключение календаря"
        verbose_name_plural = "исключения календаря"
        constraints = [
            status_check(
                "exception_type", CALENDAR_EXCEPTION_TYPES,
                "ck_calendar_exceptions_exception_type",
            ),
            # Одна дата — одно исключение: отдельно для офиса и отдельно
            # для всей организации.
            models.UniqueConstraint(
                fields=["office", "date"],
                condition=models.Q(office__isnull=False),
                name="uq_calendar_exceptions_office_date",
            ),
            models.UniqueConstraint(
                fields=["organization", "date"],
                condition=models.Q(office__isnull=True),
                name="uq_calendar_exceptions_org_date",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_calendar_exceptions_organization_id"
            ),
            models.Index(fields=["office"], name="ix_calendar_exceptions_office_id"),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.name}"
