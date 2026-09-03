"""Django Admin: аварийный вход, а не рабочий интерфейс.

Проверяется главное — кого пускают, кого нет и что нельзя удалить.
Это интерфейс последней надежды: он должен работать, когда обычный путь
недоступен, и не должен быть способом обойти правила, когда путь доступен.
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.core.management import call_command

from humotech.accounts.models import User
from humotech.audit.models import AuditLog
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.offices.models import Office
from humotech.organizations.models import Organization
from humotech.rbac.models import Permission, Role

pytestmark = pytest.mark.django_db


@pytest.fixture()
def seeded_organization(db) -> Organization:
    call_command("seed", create_organization="ADM", verbosity=0)
    return Organization.objects.get(code="ADM")


def test_operator_tables_are_registered():
    """В аварийной ситуации нужны именно эти таблицы."""
    registered = set(admin.site._registry)
    for model in (Organization, Office, Employee, User, Role, Permission,
                  AuditLog, EmployeeAssignment):
        assert model in registered, f"{model.__name__} не зарегистрирована"


def test_nothing_can_be_deleted_through_admin():
    """Историю нельзя сносить из интерфейса.

    Внешние ключи с `ON DELETE RESTRICT` всё равно не дадут удалить строку
    со связями — кнопка только создавала бы ложное ожидание.
    """
    for model, model_admin in admin.site._registry.items():
        if model._meta.app_label.startswith(("auth", "contenttypes")):
            continue
        assert model_admin.has_delete_permission(None) is False, (
            f"{model.__name__}: удаление из админки должно быть закрыто"
        )


def test_audit_log_and_permissions_are_read_only():
    """Аудит, который можно отредактировать, аудитом не является.
    Каталог разрешений — часть контракта и правится кодом, а не руками."""
    for model in (AuditLog, Permission):
        model_admin = admin.site._registry[model]
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_change_permission(None) is False


def test_assignments_are_read_only_because_they_are_periods():
    """Назначения — периоды. Правка одной строки разрушила бы историю,
    по которой считается прошлое рабочее время."""
    model_admin = admin.site._registry[EmployeeAssignment]
    assert model_admin.has_change_permission(None) is False


def test_password_is_not_editable_in_user_admin():
    """В базе только хеш. Поле для пароля в аварийном интерфейсе — прямой
    способ записать туда открытый пароль."""
    assert "password" in admin.site._registry[User].exclude


def test_only_super_admin_gets_into_admin(seeded_organization, make_actor):
    from humotech.rbac.models import RolePermission

    super_role = Role.objects.get(code="SUPER_ADMIN", organization__isnull=True)
    tech_role = Role.objects.get(code="TECH_ADMIN", organization__isnull=True)

    def user_with(role: Role) -> User:
        from humotech.accounts.models import UserRoleScope

        user = User(
            organization=seeded_organization,
            email=f"{role.code.lower()}@humotech.tj", status="ACTIVE",
        )
        user.set_password("Очень-Длинный-Пароль-2026")
        user.save()
        UserRoleScope.objects.create(
            organization=seeded_organization, user=user, role=role
        )
        return user

    assert user_with(super_role).is_staff is True
    assert user_with(tech_role).is_staff is False, (
        "техадмин работает через CRM: там права проверяются доменным "
        "каталогом, а не кодами моделей Django"
    )
    assert RolePermission.objects.filter(role=tech_role).exists(), (
        "у техадмина есть свои права — просто не на админку"
    )


def test_inactive_user_is_not_staff(seeded_organization):
    from humotech.accounts.models import UserRoleScope

    role = Role.objects.get(code="SUPER_ADMIN", organization__isnull=True)
    user = User(
        organization=seeded_organization, email="blocked@humotech.tj",
        status="LOCKED",
    )
    user.set_password("Очень-Длинный-Пароль-2026")
    user.save()
    UserRoleScope.objects.create(
        organization=seeded_organization, user=user, role=role
    )

    assert user.is_active is False
    assert user.has_perm("employees.manage") is False
