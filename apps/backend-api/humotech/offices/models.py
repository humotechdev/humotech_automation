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
    #: Адрес. Пусто — офис завели, но ещё не настроили: адрес, точку на
    #: карте и радиус задают в карточке офиса. Требовать адрес в момент
    #: создания значит не дать завести офис тому, кто его ещё не знает.
    address = models.TextField(null=True, blank=True)
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
    #: Форма геозоны: круг (точка и радиус) или контур территории. Старые
    #: офисы остаются кругом — о контуре они не знают, и менять им
    #: правило молча нельзя.
    geofence_kind = models.CharField(
        max_length=10,
        choices=[("CIRCLE", "Круг"), ("POLYGON", "Контур территории")],
        default="CIRCLE",
        db_default="CIRCLE",
    )
    #: Контур: `[[широта, долгота], …]`, незамкнутый, от 3 до 50 углов.
    #: Проверяется сервисом (`offices.geo.clean_polygon`) — база только
    #: не даёт выбрать контур, не сохранив его.
    geofence_polygon = models.JSONField(null=True, blank=True)
    #: Новое местоположение, ждущее подтверждения. Пока его не подтвердили,
    #: отметки сверяются с прежним: у офиса, где уже отмечаются, не бывает
    #: минуты «без места» и не бывает места, которое никто не проверил.
    #: Вид: `{"geofence_kind", "latitude", "longitude", "geofence_radius_m",
    #: "geofence_polygon"}` — те же поля, что у действующей геозоны.
    pending_location = models.JSONField(null=True, blank=True)
    pending_location_by_user = models.ForeignKey(
        "accounts.User",
        # Учётку HR можно удалить, а предложенное место остаётся ждать
        # решения — пропадает только подпись (как у автора QR-точки).
        on_delete=models.SET_NULL,
        db_column="pending_location_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    pending_location_at = models.DateTimeField(null=True, blank=True)
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
                condition=Q(geofence_kind__in=("CIRCLE", "POLYGON")),
                name="ck_offices_geofence_kind",
            ),
            models.CheckConstraint(
                condition=~Q(geofence_kind="POLYGON")
                | Q(geofence_polygon__isnull=False),
                name="ck_offices_polygon_present",
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
