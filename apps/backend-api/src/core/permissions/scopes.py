"""Разрешение области видимости пользователя CRM.

Ровно та логика, что описана в `user_role_scopes`:

    region_id IS NULL и office_id IS NULL  -> вся организация
    указан region_id                        -> все офисы этого региона
    указан office_id                        -> только этот офис

Функции возвращают множество офисов, данные которых пользователь имеет право
видеть. `None` означает «вся организация» — это не то же самое, что пустое
множество (пустое = не видит ничего).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.offices.models import Office
from src.modules.roles.models import Role, RolePermission, UserRoleScope


def active_scopes(
    session: Session, *, user_id: uuid.UUID, at: datetime | None = None
) -> list[UserRoleScope]:
    at = at or datetime.now(tz=timezone.utc)
    return list(
        session.scalars(
            select(UserRoleScope).where(
                UserRoleScope.user_id == user_id,
                UserRoleScope.valid_from <= at,
                (UserRoleScope.valid_to.is_(None)) | (UserRoleScope.valid_to >= at),
            )
        )
    )


def visible_office_ids(
    session: Session, *, user_id: uuid.UUID, at: datetime | None = None
) -> set[uuid.UUID] | None:
    """Офисы, доступные пользователю. None — доступ ко всей организации."""
    scopes = active_scopes(session, user_id=user_id, at=at)
    if not scopes:
        return set()

    office_ids: set[uuid.UUID] = set()
    region_ids: set[uuid.UUID] = set()

    for scope in scopes:
        if scope.region_id is None and scope.office_id is None:
            return None  # вся организация
        if scope.office_id is not None:
            office_ids.add(scope.office_id)
        if scope.region_id is not None:
            region_ids.add(scope.region_id)

    if region_ids:
        office_ids.update(
            session.scalars(
                select(Office.id).where(Office.region_id.in_(region_ids))
            )
        )
    return office_ids


def can_see_office(
    session: Session,
    *,
    user_id: uuid.UUID,
    office_id: uuid.UUID,
    at: datetime | None = None,
) -> bool:
    visible = visible_office_ids(session, user_id=user_id, at=at)
    return True if visible is None else office_id in visible


def permission_codes(
    session: Session, *, user_id: uuid.UUID, at: datetime | None = None
) -> set[str]:
    """Объединение разрешений всех действующих ролей пользователя."""
    scopes = active_scopes(session, user_id=user_id, at=at)
    if not scopes:
        return set()
    role_ids = {scope.role_id for scope in scopes}
    from src.modules.roles.models import Permission

    return set(
        session.scalars(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .where(Role.id.in_(role_ids))
        )
    )
