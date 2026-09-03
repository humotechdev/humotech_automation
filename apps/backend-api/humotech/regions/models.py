"""Регион — группа офисов. Основа регионального разграничения прав HR."""

from __future__ import annotations

from django.db import models

from humotech.core.enums import REGION_STATUSES, choices, status_check
from humotech.core.models import (
    ArchivableModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class Region(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    # NULL — регион наследует organizations.default_timezone
    timezone = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=30, choices=choices(REGION_STATUSES))

    class Meta:
        db_table = "regions"
        verbose_name = "регион"
        verbose_name_plural = "регионы"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uq_regions_org_code"
            ),
            status_check("status", REGION_STATUSES, "ck_regions_status"),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_regions_organization_id"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"
