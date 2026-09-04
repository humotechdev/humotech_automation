"""Разрешения на управление привязкой Telegram.

Каталог разрешений — часть контракта данных, а не код: он живёт в таблице
`permissions`, и добавление кода в `permissions_catalog.py` само по себе
ничего не меняет в уже развёрнутой базе. Отсюда правило проекта: новое
разрешение — правка каталога ПЛЮС миграция, доливающая строки.

Коды и роли здесь записаны буквально, а не взяты из каталога. Миграция
описывает состояние базы на свой момент времени; если она будет читать
изменяемый модуль, то через полгода воспроизведёт не то, что применялось.

Идемпотентна: повторное применение ничего не дублирует. Откат снимает
только эти две строки и их привязки к ролям.
"""

from django.db import migrations

PERMISSIONS = [
    ("telegram.read", "Просмотр привязок Telegram",
     "Видеть состояние привязки и заявки, ожидающие подтверждения"),
    ("telegram.manage", "Управление привязками Telegram",
     "Выдавать ссылки, подтверждать, отклонять и отключать привязку"),
]

# Системные роли (organization_id IS NULL) и то, что им достаётся.
ROLE_GRANTS = {
    "SUPER_ADMIN": ("telegram.read", "telegram.manage"),
    "HR_ADMIN": ("telegram.read", "telegram.manage"),
    "REGIONAL_HR": ("telegram.read", "telegram.manage"),
    "OFFICE_ADMIN": ("telegram.read",),
}


def add_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permission_ids = {}
    for code, name, description in PERMISSIONS:
        row, _ = Permission.objects.get_or_create(
            code=code, defaults={"name": name, "description": description}
        )
        permission_ids[code] = row.id

    for role_code, codes in ROLE_GRANTS.items():
        role = Role.objects.filter(
            code=role_code, organization__isnull=True
        ).first()
        # Роли может не быть: базу могли развернуть без `manage.py seed`.
        # Тогда доливать нечего — seed создаст роль уже с этими правами.
        if role is None:
            continue
        for code in codes:
            RolePermission.objects.get_or_create(
                role_id=role.id, permission_id=permission_ids[code]
            )


def remove_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")

    codes = [code for code, _, _ in PERMISSIONS]
    RolePermission.objects.filter(permission__code__in=codes).delete()
    Permission.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [("rbac", "0001_initial")]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
