"""Права демонстрационной роли: воспроизводимо и без лишнего.

Прежнее поведение — раздача только внутри `if created` — означало, что
разрешение, добавленное в каталог позже, на уже заведённом стенде не
появлялось никогда. Чинилось это разовой вставкой в базу; здесь
проверяется, что такой шаг больше не нужен.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from humotech.core.management.commands.demo_data import (
    DEMO_ROLE_REQUIRED,
    NEVER_GRANTED_BY_DEMO,
)
from humotech.core.permissions_catalog import ROLE_PERMISSIONS
from humotech.rbac.models import Permission, Role, RolePermission


def codes_of(role: Role) -> set[str]:
    return set(
        RolePermission.objects.filter(role=role).values_list(
            "permission__code", flat=True
        )
    )


@pytest.fixture()
def catalogue(db):
    """Каталог разрешений: команда выдаёт только то, что в нём есть."""
    call_command("seed")


def demo_role() -> Role:
    return Role.objects.get(code="HR_ADMIN_LOCAL")


class TestFreshStand:
    def test_новая_роль_получает_набор_кадрового_администратора(
        self, catalogue
    ):
        call_command("demo_data", "--yes", verbosity=0)

        assert codes_of(demo_role()) == set(ROLE_PERMISSIONS["HR_ADMIN"])


class TestExistingStand:
    def test_существующая_роль_добирает_недостающее(self, catalogue):
        call_command("demo_data", "--yes", verbosity=0)
        role = demo_role()
        RolePermission.objects.filter(
            role=role, permission__code__in=DEMO_ROLE_REQUIRED
        ).delete()
        assert not (codes_of(role) & set(DEMO_ROLE_REQUIRED))

        call_command("demo_data", "--yes", verbosity=0)

        assert set(DEMO_ROLE_REQUIRED) <= codes_of(demo_role())

    def test_согласованные_права_сохраняются(self, catalogue):
        call_command("demo_data", "--yes", verbosity=0)
        role = demo_role()
        extra = Permission.objects.get(code="attendance.read")
        before = codes_of(role)

        call_command("demo_data", "--yes", verbosity=0)

        after = codes_of(demo_role())
        assert before <= after
        assert extra.code in after

    def test_повторный_запуск_не_плодит_дубликатов(self, catalogue):
        call_command("demo_data", "--yes", verbosity=0)
        first = RolePermission.objects.filter(role=demo_role()).count()

        call_command("demo_data", "--yes", verbosity=0)
        call_command("demo_data", "--yes", verbosity=0)

        assert RolePermission.objects.filter(role=demo_role()).count() == first

    def test_существующей_роли_не_достаётся_административных_прав(
        self, catalogue
    ):
        """Слепая синхронизация с каталогом выдала бы и audit.read."""
        call_command("demo_data", "--yes", verbosity=0)
        role = demo_role()
        RolePermission.objects.filter(
            role=role, permission__code__in=NEVER_GRANTED_BY_DEMO
        ).delete()

        call_command("demo_data", "--yes", verbosity=0)

        assert not (codes_of(demo_role()) & set(NEVER_GRANTED_BY_DEMO))

    def test_другие_роли_не_меняются(self, catalogue):
        call_command("demo_data", "--yes", verbosity=0)
        others = {
            role.code: codes_of(role)
            for role in Role.objects.exclude(code="HR_ADMIN_LOCAL")
        }

        call_command("demo_data", "--yes", verbosity=0)

        assert {
            role.code: codes_of(role)
            for role in Role.objects.exclude(code="HR_ADMIN_LOCAL")
        } == others


class TestMissingPermission:
    def test_отсутствие_разрешения_в_каталоге_объявляется(
        self, catalogue, capsys
    ):
        """Права нет — раздел не откроется; молчать об этом нельзя."""
        call_command("demo_data", "--yes", verbosity=0)
        role = demo_role()
        RolePermission.objects.filter(
            role=role, permission__code__in=DEMO_ROLE_REQUIRED
        ).delete()
        Permission.objects.filter(code__in=DEMO_ROLE_REQUIRED).delete()

        call_command("demo_data", "--yes", verbosity=0)

        printed = capsys.readouterr().out
        assert "В каталоге разрешений нет" in printed
        for code in DEMO_ROLE_REQUIRED:
            assert code in printed


class TestListItself:
    def test_список_не_содержит_административных_разрешений(self):
        assert not (set(DEMO_ROLE_REQUIRED) & set(NEVER_GRANTED_BY_DEMO))
