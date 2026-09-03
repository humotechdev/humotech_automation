"""Отсутствия: типы, заявки, подтверждённые периоды, документы, балансы.

Заявка и само отсутствие — разные сущности. Заявка — намерение и его
рассмотрение; отсутствие — факт, по которому считается рабочее время.
Одна отклонённая заявка не должна оставлять следа в расчётах.
"""

from __future__ import annotations

from django.db import models

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    ABSENCE_ACTIONS,
    ABSENCE_REQUEST_KINDS,
    ABSENCE_REQUEST_STATUSES,
    DOCUMENT_VERIFICATION_STATUSES,
    EMPLOYEE_ABSENCE_STATUSES,
    choices,
    status_check,
)
from humotech.core.models import (
    CreatedAtModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class AbsenceType(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Справочник видов отсутствия: больничный, отпуск, командировка."""

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    is_paid = models.BooleanField(db_default=False)
    requires_approval = models.BooleanField(db_default=True)
    requires_document = models.BooleanField(db_default=False)
    document_required_after_days = models.IntegerField(null=True, blank=True)
    deducts_leave_balance = models.BooleanField(db_default=False)
    is_active = models.BooleanField(db_default=True)

    class Meta:
        db_table = "absence_types"
        verbose_name = "вид отсутствия"
        verbose_name_plural = "виды отсутствия"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uq_absence_types_org_code"
            ),
            raw_check(
                "document_required_after_days IS NULL "
                "OR document_required_after_days >= 0",
                "ck_absence_types_document_days_non_negative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_absence_types_organization_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class AbsenceRequest(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Заявка сотрудника. Продление и отмена ссылаются на исходную заявку."""

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="absence_requests",
    )
    absence_type = models.ForeignKey(
        AbsenceType,
        on_delete=models.PROTECT,
        db_column="absence_type_id",
        db_index=False,
        related_name="requests",
    )
    request_kind = models.CharField(
        max_length=20, choices=choices(ABSENCE_REQUEST_KINDS)
    )
    parent_request = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        db_column="parent_request_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="derived_requests",
    )
    requested_start_at = models.DateTimeField(null=True, blank=True)
    requested_end_at = models.DateTimeField(null=True, blank=True)
    employee_comment = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=choices(ABSENCE_REQUEST_STATUSES)
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="reviewed_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "absence_requests"
        verbose_name = "заявка на отсутствие"
        verbose_name_plural = "заявки на отсутствие"
        constraints = [
            status_check(
                "request_kind", ABSENCE_REQUEST_KINDS,
                "ck_absence_requests_request_kind",
            ),
            status_check(
                "status", ABSENCE_REQUEST_STATUSES, "ck_absence_requests_status"
            ),
            raw_check(
                "requested_end_at IS NULL OR requested_start_at IS NULL "
                "OR requested_end_at >= requested_start_at",
                "ck_absence_requests_end_after_start",
            ),
            # Продление и отмена бессмысленны без исходной заявки.
            raw_check(
                "request_kind = 'CREATE' OR parent_request_id IS NOT NULL",
                "ck_absence_requests_derived_needs_parent",
            ),
            raw_check(
                "parent_request_id IS NULL OR parent_request_id <> id",
                "ck_absence_requests_no_self_parent",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_absence_requests_organization_id"
            ),
            models.Index(fields=["employee"], name="ix_absence_requests_employee_id"),
            models.Index(
                fields=["organization", "status"], name="ix_absence_requests_status"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.request_kind} {self.employee_id} ({self.status})"


class EmployeeAbsence(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Подтверждённый период отсутствия — то, что учитывается в расчётах."""

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="absences",
    )
    absence_type = models.ForeignKey(
        AbsenceType,
        on_delete=models.PROTECT,
        db_column="absence_type_id",
        db_index=False,
        related_name="absences",
    )
    origin_request = models.ForeignKey(
        AbsenceRequest,
        on_delete=models.PROTECT,
        db_column="origin_request_id",
        db_index=False,
        related_name="absences",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(
        max_length=20, choices=choices(EMPLOYEE_ABSENCE_STATUSES)
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "employee_absences"
        verbose_name = "отсутствие сотрудника"
        verbose_name_plural = "отсутствия сотрудников"
        constraints = [
            status_check(
                "status", EMPLOYEE_ABSENCE_STATUSES, "ck_employee_absences_status"
            ),
            raw_check("end_at >= start_at", "ck_employee_absences_end_after_start"),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_employee_absences_organization_id"
            ),
            models.Index(fields=["employee"], name="ix_employee_absences_employee_id"),
            models.Index(
                fields=["employee", "start_at", "end_at"],
                name="ix_employee_absences_period",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} {self.start_at:%Y-%m-%d}"


class AbsenceDocument(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Справка или больничный лист, приложенный к заявке."""

    absence_request = models.ForeignKey(
        AbsenceRequest,
        on_delete=models.PROTECT,
        db_column="absence_request_id",
        db_index=False,
        related_name="documents",
    )
    file = models.ForeignKey(
        "files.File",
        on_delete=models.PROTECT,
        db_column="file_id",
        db_index=False,
        related_name="absence_documents",
    )
    document_type = models.CharField(max_length=50)
    verification_status = models.CharField(
        max_length=20, choices=choices(DOCUMENT_VERIFICATION_STATUSES)
    )
    verified_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="verified_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_comment = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "absence_documents"
        verbose_name = "документ отсутствия"
        verbose_name_plural = "документы отсутствия"
        constraints = [
            models.UniqueConstraint(
                fields=["absence_request", "file"], name="uq_absence_documents_file"
            ),
            status_check(
                "verification_status", DOCUMENT_VERIFICATION_STATUSES,
                "ck_absence_documents_verification_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_absence_documents_organization_id"
            ),
            models.Index(
                fields=["absence_request"],
                name="ix_absence_documents_absence_request_id",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document_type} ({self.verification_status})"


class AbsenceAction(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """История работы с заявкой. Неизменяемая: только добавление."""

    absence_request = models.ForeignKey(
        AbsenceRequest,
        on_delete=models.PROTECT,
        db_column="absence_request_id",
        db_index=False,
        related_name="actions",
    )
    action = models.CharField(max_length=30, choices=choices(ABSENCE_ACTIONS))
    actor_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="actor_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    actor_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="actor_employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    previous_status = models.CharField(max_length=20, null=True, blank=True)
    new_status = models.CharField(max_length=20, null=True, blank=True)
    comment = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "absence_actions"
        verbose_name = "действие по заявке"
        verbose_name_plural = "действия по заявкам"
        constraints = [
            status_check("action", ABSENCE_ACTIONS, "ck_absence_actions_action"),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_absence_actions_organization_id"
            ),
            models.Index(
                fields=["absence_request"],
                name="ix_absence_actions_absence_request_id",
            ),
        ]

    def __str__(self) -> str:
        return self.action


class LeaveBalance(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Баланс отпуска по годам. Всё в минутах — чтобы не спорить о дробных днях."""

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="leave_balances",
    )
    absence_type = models.ForeignKey(
        AbsenceType,
        on_delete=models.PROTECT,
        db_column="absence_type_id",
        db_index=False,
        related_name="balances",
    )
    year = models.SmallIntegerField()
    allocated_minutes = models.IntegerField(db_default=0)
    used_minutes = models.IntegerField(db_default=0)
    reserved_minutes = models.IntegerField(db_default=0)
    # может быть отрицательной: перенос и удержания
    adjustment_minutes = models.IntegerField(db_default=0)

    class Meta:
        db_table = "leave_balances"
        verbose_name = "баланс отпуска"
        verbose_name_plural = "балансы отпусков"
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "absence_type", "year"],
                name="uq_leave_balances_year",
            ),
            raw_check("year >= 2000 AND year <= 2200", "ck_leave_balances_year_range"),
            raw_check(
                "allocated_minutes >= 0", "ck_leave_balances_allocated_non_negative"
            ),
            raw_check("used_minutes >= 0", "ck_leave_balances_used_non_negative"),
            raw_check(
                "reserved_minutes >= 0", "ck_leave_balances_reserved_non_negative"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_leave_balances_organization_id"
            ),
            models.Index(fields=["employee"], name="ix_leave_balances_employee_id"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} {self.year}"
