"""Область видимости: региональный HR не должен видеть чужие офисы.

Перенос `tests/integration/test_permission_scopes.py`. Прежние отдельные
функции `visible_office_ids` и `can_see_office` стали методами `AccessControl`
— проверяется то же самое поведение, через тот же слой прав, которым
пользуются сервисы.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from humotech.core.errors import NotFound, PermissionDenied
from humotech.core.rbac import AccessControl

pytestmark = pytest.mark.django_db


def test_regional_hr_sees_only_its_region(
    organization, region, other_region, office, other_office, make_actor
):
    actor = make_actor(organization, permissions=("offices.read",), region=region)
    access = AccessControl()

    assert access.visible_office_ids(actor) == {office.id}
    assert access.require_office(actor, office.id).id == office.id
    with pytest.raises(PermissionDenied):
        access.require_office(actor, other_office.id)


def test_office_admin_sees_only_its_office(
    organization, office, other_office, make_actor
):
    actor = make_actor(
        organization, permissions=("offices.read",), office=other_office
    )
    access = AccessControl()

    assert access.visible_office_ids(actor) == {other_office.id}
    with pytest.raises(PermissionDenied):
        access.require_office(actor, office.id)


def test_hr_admin_without_scope_sees_whole_organization(
    organization, office, other_office, make_actor
):
    """`region_id IS NULL` и `office_id IS NULL` — доступ ко всей организации.

    Именно этот случай отличается от «не видит ничего»: там пустое множество,
    здесь None. Склеить их — значит выдать всю организацию тому, у кого прав нет.
    """
    actor = make_actor(organization, permissions=("offices.read",))
    access = AccessControl()

    assert access.visible_office_ids(actor) is None
    assert access.require_office(actor, office.id).id == office.id
    assert access.require_office(actor, other_office.id).id == other_office.id


def test_expired_grant_gives_neither_permissions_nor_scope(
    organization, office, make_actor, now
):
    actor = make_actor(
        organization, permissions=("offices.read",), office=office,
        valid_from=now - timedelta(days=10), valid_to=now - timedelta(days=1),
    )
    access = AccessControl()

    assert access.permissions(actor) == set()
    assert access.visible_office_ids(actor) == set(), (
        "истёкшая выдача — это «ничего», а не «вся организация»"
    )


def test_office_of_another_organization_is_not_found(
    organization, make_actor, foreign_office
):
    """Чужая организация отвечает как несуществующая запись.

    Отличать «нет такого офиса» от «офис есть, но чужой» нельзя: перебором
    идентификаторов так можно пересчитать записи соседей.
    """
    actor = make_actor(organization, permissions=("offices.read",))
    with pytest.raises(NotFound):
        AccessControl().require_office(actor, foreign_office.id)
