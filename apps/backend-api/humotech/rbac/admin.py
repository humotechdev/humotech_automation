from django.contrib import admin

from humotech.core.admin import OperationalAdmin, ReadOnlyAdmin
from humotech.rbac.models import Permission, Role


@admin.register(Permission)
class PermissionAdmin(ReadOnlyAdmin):
    """Только чтение: каталог разрешений — часть контракта.

    Он живёт в `humotech/core/permissions_catalog.py` и наполняется командой
    `seed`. Разрешение, добавленное мимо каталога, не будет знать ни один
    сервис, зато будет выглядеть настоящим.
    """

    list_display = ("code", "name")
    search_fields = ("code", "name")
    ordering = ("code",)


@admin.register(Role)
class RoleAdmin(OperationalAdmin):
    list_display = ("code", "name", "organization", "is_system")
    list_filter = ("is_system", "organization")
    search_fields = ("code", "name")
    ordering = ("code",)


# `RolePermission` в админке не зарегистрирована: у неё составной первичный
# ключ, а Django Admin такие модели не поддерживает. Терять это нечего —
# привязка разрешений к системным ролям задаётся каталогом
# (`humotech/core/permissions_catalog.py`) и командой `seed`, а не руками.
