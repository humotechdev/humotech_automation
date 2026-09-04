"""Разрешения на очередь уведомлений и чтение аудита кадровиком.

Каталог разрешений живёт в таблице, а не в коде: правка
`permissions_catalog.py` сама по себе ничего не меняет в развёрнутой базе.
Поэтому новое разрешение — правка каталога ПЛЮС эта миграция.

Два изменения, и второе важнее первого.

**Уведомления.** Ничего подходящего в каталоге не было: `telegram.manage`
отвечает на вопрос «кому открыт вход», а не «кто может переотправить
сообщение». Чтение и вмешательство разделены намеренно — посмотреть,
почему не дошло, безопасно; повторная отправка доходит до сотрудника.

**`audit.read` кадровому администратору.** До сих пор журнал изменений
читали только SUPER_ADMIN и TECH_ADMIN, то есть кадровик получал 403 на
собственных кадровых операциях. Разрешение НЕ снимает область: записи
по-прежнему ограничены организацией, проверка идёт отдельно от права.
Прав на учётные записи, роли и настройки кадровику здесь не выдаётся:
управление доступом — не кадровая операция.

Коды и роли записаны буквально, а не прочитаны из каталога: миграция
описывает состояние базы на свой момент, и чтение изменяемого модуля
через полгода воспроизвело бы не то, что применялось.

Идемпотентна. Откат снимает только эти строки и их привязки.
"""

from django.db import migrations

PERMISSIONS = [
    ("notifications.read", "Просмотр уведомлений",
     "Видеть очередь отправки, попытки и причины отказов"),
    ("notifications.manage", "Управление уведомлениями",
     "Повторять отправку и отменять неотправленные уведомления"),
]

# Системные роли (organization_id IS NULL) и то, что им достаётся.
ROLE_GRANTS = {
    "SUPER_ADMIN": ("notifications.read", "notifications.manage"),
    "HR_ADMIN": ("notifications.read", "notifications.manage", "audit.read"),
    "REGIONAL_HR": ("notifications.read",),
    "OFFICE_ADMIN": ("notifications.read",),
    "TECH_ADMIN": ("notifications.read", "notifications.manage"),
}

# Что откат обязан снять с ролей, не удаляя само разрешение: `audit.read`
# существовало до этой миграции и принадлежит TECH_ADMIN.
REVOKE_ON_ROLLBACK = {"HR_ADMIN": ("audit.read",)}


def add_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    for code, name, description in PERMISSIONS:
        Permission.objects.get_or_create(
            code=code, defaults={"name": name, "description": description}
        )

    for role_code, codes in ROLE_GRANTS.items():
        role = Role.objects.filter(
            code=role_code, organization__isnull=True
        ).first()
        # Роли может не быть: базу могли развернуть без `manage.py seed`.
        # Тогда доливать нечего — seed создаст роль уже с этими правами.
        if role is None:
            continue
        for code in codes:
            permission = Permission.objects.filter(code=code).first()
            if permission is None:
                continue
            RolePermission.objects.get_or_create(
                role_id=role.id, permission_id=permission.id
            )


def remove_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    for role_code, codes in REVOKE_ON_ROLLBACK.items():
        role = Role.objects.filter(
            code=role_code, organization__isnull=True
        ).first()
        if role is None:
            continue
        RolePermission.objects.filter(
            role_id=role.id, permission__code__in=list(codes)
        ).delete()

    codes = [code for code, _, _ in PERMISSIONS]
    RolePermission.objects.filter(permission__code__in=codes).delete()
    Permission.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [("rbac", "0002_telegram_permissions")]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
