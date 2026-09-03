"""Должность. Общая для всей организации, не привязана к офису."""

from __future__ import annotations

from django.db import models

from humotech.core.enums import POSITION_STATUSES, choices, status_check
from humotech.core.models import (
    ArchivableModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class Position(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=choices(POSITION_STATUSES))

    class Meta:
        db_table = "positions"
        verbose_name = "должность"
        verbose_name_plural = "должности"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uq_positions_org_code"
            ),
            status_check("status", POSITION_STATUSES, "ck_positions_status"),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_positions_organization_id"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"
