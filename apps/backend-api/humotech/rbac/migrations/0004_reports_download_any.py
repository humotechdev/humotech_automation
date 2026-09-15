"""Право скачивать чужие выгрузки: `reports.download_any`.

До сих пор чужой файл отдавался любому с `audit.read` и областью на всю
организацию. Чтение журнала — не то же решение, что выдача содержимого
файла с персональными данными, поэтому право отдельное.

Выдаётся SUPER_ADMIN (у него весь каталог) и HR_ADMIN. Остальным — только
явным назначением. Коды записаны буквально: миграция описывает состояние
базы на свой момент. Идемпотентна; откат снимает только эти строки.
"""

from django.db import migrations

PERMISSION = (
    "reports.download_any",
    "Скачивание чужих выгрузок",
    "Скачивать отчёты других пользователей, если область видимости "
    "покрывает все данные отчёта",
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

    dependencies = [("rbac", "0003_notifications_and_hr_audit")]

    operations = [
        migrations.RunPython(add_permission, remove_permission),
    ]
