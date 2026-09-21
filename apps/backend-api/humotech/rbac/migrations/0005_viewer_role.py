"""Системная роль «Просмотр»: смотреть можно, менять нельзя.

В каталоге ролей до сих пор не было той, что даёт только чтение.
Ближайшая, `ACCOUNTANT`, умеет согласовывать отсутствия — а нужна роль
для человека, которому показывают данные и ничего не доверяют менять:
стажёр, аудитор, руководитель из смежного подразделения.

Набор прав — только `*.read` и аналитика. Ни одного `manage`, `approve`
или `export`: выгрузка уносит персональные данные за пределы системы, и
для наблюдателя это уже действие, а не просмотр.

Миграция идемпотентна. Откат снимает роль целиком, но только если по
ней никому не выдан доступ: снести роль вместе с чьими-то полномочиями
значит тихо отобрать доступ у живого человека.
"""

from django.db import migrations

CODE = "VIEWER"
NAME = "Просмотр"
DESCRIPTION = (
    "Только чтение: списки, карточки и аналитика. Изменять данные, "
    "согласовывать заявки и выгружать отчёты нельзя"
)

PERMISSIONS = (
    "employees.read",
    "offices.read",
    "attendance.read",
    "schedules.read",
    "absences.read",
    "knowledge.read",
    "analytics.read",
)


def add_role(apps, schema_editor):
    Role = apps.get_model("rbac", "Role")
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")

    role, _ = Role.objects.get_or_create(
        code=CODE,
        organization=None,
        defaults={"name": NAME, "description": DESCRIPTION, "is_system": True},
    )
    # Права берутся только те, что уже есть в каталоге: заводить здесь
    # новое разрешение значило бы раздавать право, которого не проверяет
    # ни один сервис.
    for permission in Permission.objects.filter(code__in=PERMISSIONS):
        RolePermission.objects.get_or_create(
            role_id=role.id, permission_id=permission.id
        )


def remove_role(apps, schema_editor):
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserRoleScope = apps.get_model("accounts", "UserRoleScope")

    role = Role.objects.filter(code=CODE, organization__isnull=True).first()
    if role is None:
        return
    if UserRoleScope.objects.filter(role_id=role.id).exists():
        # Роль кому-то выдана. Откат миграции — не повод молча лишить
        # человека доступа: связи остаются, роль остаётся.
        return
    RolePermission.objects.filter(role_id=role.id).delete()
    Role.objects.filter(id=role.id).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0004_reports_download_any"),
        ("accounts", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(add_role, remove_role),
    ]
