"""Роли, разрешения и их связка.

Отдельное приложение, а не часть `accounts`, по двум причинам. Первая —
предметная: набор разрешений живёт своей жизнью и не зависит от учётных
записей. Вторая — техническая: `accounts` и `employees` ссылаются друг
на друга (у учётной записи есть сотрудник, а у доступа к офису — выдавший
его пользователь), поэтому Django выносит их внешние ключи в отдельную
миграцию. Составной первичный ключ `role_permissions` так создать нельзя:
он объявляется вместе с таблицей, а его колонки появились бы позже.
"""

from __future__ import annotations

from django.db import models

from humotech.core.models import (
    CreatedAtModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class Role(UUIDPrimaryKeyModel, TimestampedModel):
    """`organization_id NULL` — системная роль, общая для всех организаций."""

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        db_column="organization_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="roles",
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    is_system = models.BooleanField(db_default=False)

    class Meta:
        db_table = "roles"
        verbose_name = "роль"
        verbose_name_plural = "роли"
        constraints = [
            # Системные роли уникальны глобально...
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(organization__isnull=True),
                name="uq_roles_system_code",
            ),
            # ...а роли организации — внутри своей организации.
            models.UniqueConstraint(
                fields=["organization", "code"],
                condition=models.Q(organization__isnull=False),
                name="uq_roles_org_code",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_roles_organization_id"),
        ]

    def __str__(self) -> str:
        return self.code


class Permission(UUIDPrimaryKeyModel, CreatedAtModel):
    """Справочник разрешений. Общий для всех организаций, меняется миграциями.

    Это НЕ `django.contrib.auth.Permission`: та таблица привязана к моделям
    и действиям add/change/delete, а здесь разрешения описывают операции
    предметной области («уволить сотрудника», «опубликовать статью»).
    """

    code = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "permissions"
        verbose_name = "разрешение"
        verbose_name_plural = "разрешения"
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uq_permissions_code"),
        ]

    def __str__(self) -> str:
        return self.code


class RolePermission(CreatedAtModel):
    """Связка роль-разрешение. Чисто техническая таблица, отсюда CASCADE.

    Первичный ключ составной — суррогатного `id` здесь нет намеренно: пара
    (роль, разрешение) и есть идентичность строки, а лишняя колонка означала бы
    возможность двух одинаковых связок.
    """

    pk = models.CompositePrimaryKey("role", "permission")
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        db_column="role_id",
        db_index=False,
        related_name="permission_links",
    )
    permission = models.ForeignKey(
        Permission,
        on_delete=models.CASCADE,
        db_column="permission_id",
        db_index=False,
        related_name="role_links",
    )

    class Meta:
        db_table = "role_permissions"
        verbose_name = "разрешение роли"
        verbose_name_plural = "разрешения ролей"

    def __str__(self) -> str:
        return f"{self.role_id} -> {self.permission_id}"
