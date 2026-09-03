"""Организация — корень изоляции данных. Всё остальное живёт под ней."""

from __future__ import annotations

from django.db import models
from django.db.models.functions import Lower

from humotech.core.enums import ORGANIZATION_STATUSES, choices, status_check
from humotech.core.models import (
    ArchivableModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class Organization(UUIDPrimaryKeyModel, TimestampedModel, ArchivableModel):
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    default_timezone = models.CharField(max_length=100)
    status = models.CharField(max_length=30, choices=choices(ORGANIZATION_STATUSES))

    # Счётчик ревизии базы знаний. Растёт при каждой публикации или архивации
    # источника и входит в ключ кэша — поэтому после публикации нового правила
    # весь старый кэш ответов автоматически перестаёт использоваться,
    # без обхода и удаления ключей.
    knowledge_revision = models.IntegerField(db_default=1)

    class Meta:
        db_table = "organizations"
        verbose_name = "организация"
        verbose_name_plural = "организации"
        constraints = [
            status_check("status", ORGANIZATION_STATUSES, "ck_organizations_status"),
            # Регистронезависимая уникальность кода. Задана выражением, поэтому
            # PostgreSQL создаёт уникальный ИНДЕКС, а не ограничение, — ровно
            # как в исходной схеме.
            models.UniqueConstraint(
                Lower("code"), name="uq_organizations_lower_code"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class OrganizationSetting(UUIDPrimaryKeyModel, TimestampedModel):
    """Настройки организации как key/value — чтобы не плодить колонки."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        db_column="organization_id",
        db_index=False,
        related_name="settings",
    )
    key = models.CharField(max_length=100)
    value = models.JSONField()

    class Meta:
        db_table = "organization_settings"
        verbose_name = "настройка организации"
        verbose_name_plural = "настройки организации"
        constraints = [
            # Через выражения, а не через fields: так Django создаёт уникальный
            # ИНДЕКС, как в исходной схеме, а не UNIQUE-ограничение.
            models.UniqueConstraint(
                models.F("organization"), models.F("key"),
                name="uq_organization_settings_org_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_organization_settings_organization_id"
            ),
        ]

    def __str__(self) -> str:
        return self.key
