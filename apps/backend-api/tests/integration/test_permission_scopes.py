"""Область видимости: региональный HR не должен видеть чужие офисы."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.core.permissions.scopes import (
    can_see_office,
    permission_codes,
    visible_office_ids,
)
from src.modules.roles.models import Role, UserRoleScope
from src.modules.users.models import User

pytestmark = pytest.mark.usefixtures("engine")


def _make_user(db, organization, email: str) -> User:
    user = User(
        organization_id=organization.id,
        email=email,
        password_hash="argon2:stub",
        status="ACTIVE",
    )
    db.add(user)
    db.flush()
    return user


def _role(db, code: str) -> Role:
    role = Role(code=code, name=code, is_system=True)
    db.add(role)
    db.flush()
    return role


def test_regional_hr_sees_only_its_region(db, organization, region, other_region,
                                          office, other_office, now):
    user = _make_user(db, organization, "regional@humotech.tj")
    role = _role(db, "REGIONAL_HR")
    db.add(
        UserRoleScope(
            organization_id=organization.id,
            user_id=user.id,
            role_id=role.id,
            region_id=region.id,
            valid_from=now - timedelta(days=1),
        )
    )
    db.flush()

    visible = visible_office_ids(db, user_id=user.id, at=now)
    assert visible == {office.id}
    assert can_see_office(db, user_id=user.id, office_id=office.id, at=now)
    assert not can_see_office(db, user_id=user.id, office_id=other_office.id, at=now)


def test_office_admin_sees_only_its_office(db, organization, office, other_office,
                                           now):
    user = _make_user(db, organization, "office@humotech.tj")
    role = _role(db, "OFFICE_ADMIN")
    db.add(
        UserRoleScope(
            organization_id=organization.id, user_id=user.id, role_id=role.id,
            office_id=other_office.id, valid_from=now - timedelta(days=1),
        )
    )
    db.flush()

    assert visible_office_ids(db, user_id=user.id, at=now) == {other_office.id}
    assert not can_see_office(db, user_id=user.id, office_id=office.id, at=now)


def test_hr_admin_without_scope_sees_whole_organization(db, organization, office,
                                                        other_office, now):
    """region_id IS NULL и office_id IS NULL — доступ ко всей организации."""
    user = _make_user(db, organization, "admin@humotech.tj")
    role = _role(db, "HR_ADMIN")
    db.add(
        UserRoleScope(
            organization_id=organization.id, user_id=user.id, role_id=role.id,
            valid_from=now - timedelta(days=1),
        )
    )
    db.flush()

    # None означает «вся организация», а не «ничего»
    assert visible_office_ids(db, user_id=user.id, at=now) is None
    assert can_see_office(db, user_id=user.id, office_id=office.id, at=now)
    assert can_see_office(db, user_id=user.id, office_id=other_office.id, at=now)


def test_expired_scope_gives_no_access(db, organization, office, region, now):
    user = _make_user(db, organization, "former@humotech.tj")
    role = _role(db, "REGIONAL_HR")
    db.add(
        UserRoleScope(
            organization_id=organization.id, user_id=user.id, role_id=role.id,
            region_id=region.id,
            valid_from=now - timedelta(days=30),
            valid_to=now - timedelta(days=1),
        )
    )
    db.flush()

    assert visible_office_ids(db, user_id=user.id, at=now) == set()
    assert not can_see_office(db, user_id=user.id, office_id=office.id, at=now)


def test_permissions_come_from_role(db, organization, region, now):
    from scripts.seed import seed_permissions, seed_system_roles

    seed_permissions(db)
    seed_system_roles(db)

    user = _make_user(db, organization, "acc@humotech.tj")
    accountant = db.query(Role).filter(
        Role.code == "ACCOUNTANT", Role.organization_id.is_(None)
    ).one()
    db.add(
        UserRoleScope(
            organization_id=organization.id, user_id=user.id,
            role_id=accountant.id, region_id=region.id,
            valid_from=now - timedelta(days=1),
        )
    )
    db.flush()

    codes = permission_codes(db, user_id=user.id, at=now)
    assert "reports.export" in codes
    assert "attendance.read" in codes
    # бухгалтер не управляет пользователями и не согласует отсутствия
    assert "users.manage" not in codes
    assert "absences.approve" not in codes
