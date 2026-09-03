"""Устройство сотрудника — то, с чего он сканирует QR."""

from __future__ import annotations

from django.db import models

from humotech.core.enums import DEVICE_STATUSES, choices, status_check
from humotech.core.models import (
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class EmployeeDevice(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="devices",
    )
    # Только хеш: сам идентификатор устройства — персональные данные,
    # и хранить его незачем, сравнение работает и по хешу.
    device_identifier_hash = models.TextField()
    device_name = models.CharField(max_length=255, null=True, blank=True)
    platform = models.CharField(max_length=30, null=True, blank=True)
    app_version = models.CharField(max_length=50, null=True, blank=True)
    status = models.CharField(max_length=20, choices=choices(DEVICE_STATUSES))
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField(null=True, blank=True)
    trusted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "employee_devices"
        verbose_name = "устройство сотрудника"
        verbose_name_plural = "устройства сотрудников"
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "device_identifier_hash"],
                name="uq_employee_devices_hash",
            ),
            status_check("status", DEVICE_STATUSES, "ck_employee_devices_status"),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_employee_devices_organization_id"
            ),
            models.Index(fields=["employee"], name="ix_employee_devices_employee_id"),
        ]

    def __str__(self) -> str:
        return f"{self.device_name or self.platform or 'устройство'} ({self.status})"
