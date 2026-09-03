"""Отдел внутри офиса. Поддерживает вложенность через parent_department_id."""

from __future__ import annotations

from django.db import models

from humotech.core.constraints import raw_check
from humotech.core.enums import DEPARTMENT_STATUSES, choices, status_check
from humotech.core.models import (
    ArchivableModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class Department(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        related_name="departments",
    )
    parent_department = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        db_column="parent_department_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="children",
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=30, choices=choices(DEPARTMENT_STATUSES))

    class Meta:
        db_table = "departments"
        verbose_name = "отдел"
        verbose_name_plural = "отделы"
        constraints = [
            models.UniqueConstraint(
                fields=["office", "code"], name="uq_departments_office_code"
            ),
            status_check("status", DEPARTMENT_STATUSES, "ck_departments_status"),
            # Отдел не может быть родителем самому себе. Более глубокие циклы
            # (A -> B -> A) одной строкой не проверить, поэтому их не пускает
            # прикладной слой — база видит только одну строку за раз.
            raw_check(
                "parent_department_id IS NULL OR parent_department_id <> id",
                "ck_departments_no_self_parent",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_departments_organization_id"
            ),
            models.Index(fields=["office"], name="ix_departments_office_id"),
            models.Index(
                fields=["parent_department"],
                name="ix_departments_parent_department_id",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"
