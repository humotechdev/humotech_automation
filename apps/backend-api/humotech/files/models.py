"""Загруженные файлы: справки, больничные листы, документы.

Содержимое лежит в объектном хранилище, в базе — только метаданные и ключ.
Проверка на вирусы отражена статусом: файл со `scan_status <> CLEAN`
показывать нельзя.
"""

from __future__ import annotations

from django.db import models

from humotech.core.constraints import raw_check
from humotech.core.enums import FILE_SCAN_STATUSES, choices, status_check
from humotech.core.models import (
    CreatedAtModel,
    OrganizationScopedModel,
    UUIDPrimaryKeyModel,
)


class File(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    storage_provider = models.CharField(max_length=50)
    storage_key = models.TextField()
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.BigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    scan_status = models.CharField(max_length=20, choices=choices(FILE_SCAN_STATUSES))
    uploaded_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="uploaded_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    uploaded_by_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="uploaded_by_employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "files"
        verbose_name = "файл"
        verbose_name_plural = "файлы"
        constraints = [
            # Один и тот же ключ в одном хранилище не может принадлежать
            # двум записям. Через выражения — чтобы вышел уникальный ИНДЕКС,
            # как в исходной схеме.
            models.UniqueConstraint(
                models.F("storage_provider"), models.F("storage_key"),
                name="uq_files_storage_key",
            ),
            status_check("scan_status", FILE_SCAN_STATUSES, "ck_files_scan_status"),
            raw_check("size_bytes >= 0", "ck_files_size_non_negative"),
            raw_check("char_length(checksum_sha256) = 64", "ck_files_checksum_length"),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_files_organization_id"),
        ]

    def __str__(self) -> str:
        return self.original_filename
