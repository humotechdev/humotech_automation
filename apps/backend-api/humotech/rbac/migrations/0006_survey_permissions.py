"""Права на опросы: `surveys.read` и `surveys.manage`.

Разделены намеренно. Читать ответы — значит видеть, кто из сотрудников
что написал про руководителя и зарплату; это доступ к персональным
мнениям, и он не следует автоматически из права заводить опросы. Обратно
тоже: рассылать опрос всей компании должен не всякий, кто может открыть
результаты.

Выдаются SUPER_ADMIN (у него весь каталог) и HR_ADMIN: опрос — кадровый
инструмент. Наблюдателю (`VIEWER`) даётся только чтение: смотреть можно,
рассылать нельзя. Остальным — явным назначением.

Идемпотентна; откат снимает только эти строки.
"""

from django.db import migrations

PERMISSIONS = (
    (
        "surveys.read",
        "Просмотр опросов",
        "Видеть шаблоны, рассылки и именные ответы сотрудников",
    ),
    (
        "surveys.manage",
        "Управление опросами",
        "Создавать шаблоны и рассылать опросы сотрудникам",
    ),
)

#: Кому что достаётся. Чтение шире управления — это и есть разделение.
BY_ROLE = {
    "SUPER_ADMIN": ("surveys.read", "surveys.manage"),
    "HR_ADMIN": ("surveys.read", "surveys.manage"),
    "VIEWER": ("surveys.read",),
}


def add(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    made = {}
    for code, name, description in PERMISSIONS:
        made[code], _ = Permission.objects.get_or_create(
            code=code, defaults={"name": name, "description": description}
        )

    for role_code, codes in BY_ROLE.items():
        for role in Role.objects.filter(
            code=role_code, organization__isnull=True
        ):
            for code in codes:
                RolePermission.objects.get_or_create(
                    role_id=role.id, permission_id=made[code].id
                )


def remove(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")

    codes = [code for code, _, _ in PERMISSIONS]
    RolePermission.objects.filter(permission__code__in=codes).delete()
    Permission.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [("rbac", "0005_viewer_role")]

    operations = [
        migrations.RunPython(add, remove),
    ]
