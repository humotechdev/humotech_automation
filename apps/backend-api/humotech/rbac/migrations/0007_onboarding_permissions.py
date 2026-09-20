"""Права первичного ознакомления.

Три кода, а не один, потому что это три разных решения.

`onboarding.read` — посмотреть, кто на каком разделе. Безобидно и нужно
многим: руководителю офиса, наблюдателю, кадровику.

`onboarding.manage` — позвать человека, переоткрыть ссылку, послать
напоминание. Это действие, доходящее до сотрудника в Telegram.

`policies.publish` — выпустить редакцию обязательного документа. С
момента публикации бот закрыт для всех, кто её не подтвердил: это
решение юриста и кадрового руководителя, а не дежурного HR. Поэтому его
нет ни у регионального HR, ни у администратора офиса.

Идемпотентна; откат снимает только эти строки.
"""

from django.db import migrations

PERMISSIONS = (
    (
        "onboarding.read",
        "Просмотр ознакомления",
        "Видеть прогресс сотрудников, разделы и обязательные документы",
    ),
    (
        "onboarding.manage",
        "Управление ознакомлением",
        "Править разделы, слать напоминания и повторные приглашения",
    ),
    (
        "policies.publish",
        "Публикация обязательных документов",
        "Выпускать новые редакции: до их подтверждения бот закрыт",
    ),
)

BY_ROLE = {
    "SUPER_ADMIN": ("onboarding.read", "onboarding.manage", "policies.publish"),
    "HR_ADMIN": ("onboarding.read", "onboarding.manage", "policies.publish"),
    "REGIONAL_HR": ("onboarding.read", "onboarding.manage"),
    "OFFICE_ADMIN": ("onboarding.read",),
    "VIEWER": ("onboarding.read",),
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
        for role in Role.objects.filter(code=role_code, organization__isnull=True):
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

    dependencies = [("rbac", "0006_survey_permissions")]

    operations = [
        migrations.RunPython(add, remove),
    ]
