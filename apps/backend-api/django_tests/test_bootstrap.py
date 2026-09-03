"""Разворачивается ли система с пустой базы.

Проверка, которую не заменяют ни сверка схемы, ни тесты сервисов: таблицы
могут быть на месте, а работать система не будет. Пустые `permissions`
и `roles` означают, что у любого пользователя набор разрешений пуст —
и КАЖДЫЙ вызов сервиса заканчивается отказом.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from humotech.accounts.models import User, UserRoleScope
from humotech.core.permissions_catalog import (
    ALL_PERMISSION_CODES,
    ROLE_PERMISSIONS,
    SYSTEM_ROLES,
)
from humotech.core.rbac import AccessControl, Actor
from humotech.absences.models import AbsenceType
from humotech.organizations.models import Organization
from humotech.rbac.models import Permission, Role

pytestmark = pytest.mark.django_db


def test_seed_fills_catalog_and_system_roles():
    call_command("seed", verbosity=0)

    assert set(Permission.objects.values_list("code", flat=True)) == set(
        ALL_PERMISSION_CODES
    )
    assert set(
        Role.objects.filter(organization__isnull=True).values_list("code", flat=True)
    ) == {code for code, _, _ in SYSTEM_ROLES}

    hr_admin = Role.objects.get(code="HR_ADMIN", organization__isnull=True)
    granted = set(
        Permission.objects.filter(role_links__role=hr_admin).values_list(
            "code", flat=True
        )
    )
    assert granted == set(ROLE_PERMISSIONS["HR_ADMIN"])


def test_seed_is_idempotent():
    """Повторный запуск — часть развёртывания, а не аварийная ситуация."""
    call_command("seed", verbosity=0)
    before = (Permission.objects.count(), Role.objects.count())
    call_command("seed", verbosity=0)
    assert (Permission.objects.count(), Role.objects.count()) == before


def test_seed_creates_organization_with_absence_types():
    call_command(
        "seed", create_organization="BOOT", name="Проверка развёртывания",
        verbosity=0,
    )
    organization = Organization.objects.get(code="BOOT")
    assert organization.status == "ACTIVE"
    assert AbsenceType.objects.filter(organization=organization).count() == 6


def test_superuser_can_actually_work_after_seed():
    """Учётная запись без области — неработоспособна.

    Она не попадёт в админку (`is_staff` выводится из ролей) и получит отказ
    на любой операции. Поэтому область выдаётся вместе с созданием, а не
    отдельным шагом, о котором можно забыть.
    """
    call_command("seed", create_organization="BOOT", verbosity=0)
    organization = Organization.objects.get(code="BOOT")

    user = User.objects.create_superuser(
        email="root@humotech.tj",
        password="Очень-Длинный-Пароль-2026",
        organization=organization,
    )

    assert UserRoleScope.objects.filter(user=user).count() == 1
    scope = UserRoleScope.objects.get(user=user)
    assert scope.region_id is None and scope.office_id is None, (
        "пустые регион и офис означают доступ ко всей организации"
    )

    assert user.is_active is True
    assert user.is_superuser is True
    assert user.is_staff is True, "суперпользователь не попадает в админку"
    assert user.has_perm("employees.manage") is True

    actor = Actor.from_user(user)
    assert AccessControl().permissions(actor) == set(ALL_PERMISSION_CODES)


def test_superuser_without_seed_fails_loudly():
    """Молчаливое создание нерабочей учётной записи — худший из вариантов."""
    organization = Organization.objects.create(
        code="NOSEED", name="Без справочников",
        default_timezone="Asia/Dushanbe", status="ACTIVE",
    )
    with pytest.raises(ValueError, match="manage.py seed"):
        User.objects.create_superuser(
            email="root@humotech.tj", password="Очень-Длинный-Пароль-2026",
            organization=organization,
        )


def test_user_without_organization_is_rejected():
    """Организация — корень изоляции данных, учётной записи без неё быть не может."""
    with pytest.raises(ValueError, match="организации"):
        User.objects.create_user(email="ничей@humotech.tj", password="пароль")
