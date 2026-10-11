"""Право `schedules.change_history`: правка графика задним числом.

Обычная правка графика теперь действует только с сегодняшнего дня и не
меняет прошлое (решение владельца Р-5, аудит C-10). Создать версию с
прошедшей даты — отдельная контролируемая операция с причиной и аудитом.

Выдаётся SUPER_ADMIN (у него весь каталог) и HR_ADMIN. Остальным — только
явным назначением. Идемпотентна; откат снимает только эти строки.
"""

from django.db import migrations

PERMISSION = (
    "schedules.change_history",
    "Изменение графика задним числом",
    "Создавать версию графика с прошедшей даты, с обязательной причиной",
)
ROLES = ("SUPER_ADMIN", "HR_ADMIN")


def add_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    code, name, description = PERMISSION
    permission, _ = Permission.objects.get_or_create(
        code=code, defaults={"name": name, "description": description}
    )
    for role in Role.objects.filter(code__in=ROLES, organization__isnull=True):
        RolePermission.objects.get_or_create(
            role_id=role.id, permission_id=permission.id
        )


def remove_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")

    RolePermission.objects.filter(permission__code=PERMISSION[0]).delete()
    Permission.objects.filter(code=PERMISSION[0]).delete()


class Migration(migrations.Migration):

    dependencies = [("rbac", "0007_onboarding_permissions")]

    operations = [
        migrations.RunPython(add_permission, remove_permission),
    ]
