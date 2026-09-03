"""Офис — самостоятельный объект: свой адрес, свой часовой пояс, свои QR-точки."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from humotech.core.enums import OFFICE_STATUSES, choices, status_check
from humotech.core.fields import CidrField
from humotech.core.models import (
    ArchivableModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class Office(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    region = models.ForeignKey(
        "regions.Region",
        on_delete=models.PROTECT,
        db_column="region_id",
        db_index=False,
        related_name="offices",
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    address = models.TextField()
    # Часовой пояс офиса обязателен: все TIMESTAMPTZ хранятся в UTC,
    # а «опоздал / ушёл раньше» считается в локальном времени офиса.
    timezone = models.CharField(max_length=100)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    geofence_radius_m = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=choices(OFFICE_STATUSES))
    opened_at = models.DateField(null=True, blank=True)
    closed_at = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "offices"
        verbose_name = "офис"
        verbose_name_plural = "офисы"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uq_offices_org_code"
            ),
            status_check("status", OFFICE_STATUSES, "ck_offices_status"),
            models.CheckConstraint(
                condition=Q(geofence_radius_m__isnull=True)
                | Q(geofence_radius_m__gt=0),
                name="ck_offices_geofence_positive",
            ),
            models.CheckConstraint(
                condition=Q(closed_at__isnull=True)
                | Q(opened_at__isnull=True)
                | Q(closed_at__gte=models.F("opened_at")),
                name="ck_offices_close_after_open",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_offices_organization_id"),
            models.Index(fields=["region"], name="ix_offices_region_id"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class OfficeNetwork(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Разрешённые сети офиса — дополнительная проверка при QR-отметке."""

    office = models.ForeignKey(
        Office,
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        related_name="networks",
    )
    name = models.CharField(max_length=100)
    network_cidr = CidrField()
    is_active = models.BooleanField(db_default=True)

    class Meta:
        db_table = "office_networks"
        verbose_name = "сеть офиса"
        verbose_name_plural = "сети офиса"
        constraints = [
            models.UniqueConstraint(
                fields=["office", "network_cidr"], name="uq_office_networks_cidr"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_office_networks_organization_id"
            ),
            models.Index(fields=["office"], name="ix_office_networks_office_id"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.network_cidr})"
