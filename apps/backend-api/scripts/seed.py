"""Наполнение справочников. Скрипт идемпотентен — можно запускать повторно.

Что делает:
  * `permissions`      — каталог разрешений (общий для всех организаций);
  * `roles`            — семь системных ролей (organization_id IS NULL);
  * `role_permissions` — привязка разрешений к ролям;
  * `absence_types`    — типы отсутствий, но уже ДЛЯ КОНКРЕТНОЙ организации,
                         потому что у таблицы organization_id NOT NULL.

Запуск:
    python -m scripts.seed                          # только глобальные справочники
    python -m scripts.seed --organization-code HUMO # плюс типы отсутствий этой организации
    python -m scripts.seed --create-organization HUMO --name "HUMOTECH" --timezone Asia/Dushanbe
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.session import session_scope
from src.core.permissions.catalog import (
    PERMISSIONS,
    ROLE_PERMISSIONS,
    SYSTEM_ROLES,
)
from src.modules.absences.models import AbsenceType
from src.modules.organizations.models import Organization
from src.modules.roles.models import Permission, Role, RolePermission

# code, name, is_paid, requires_approval, requires_document,
# document_required_after_days, deducts_leave_balance
DEFAULT_ABSENCE_TYPES: tuple[tuple[str, str, bool, bool, bool, int | None, bool], ...] = (
    ("SICK_LEAVE", "Больничный", True, True, True, 3, False),
    ("ANNUAL_LEAVE", "Ежегодный отпуск", True, True, False, None, True),
    ("UNPAID_LEAVE", "Отпуск за свой счёт", False, True, False, None, False),
    ("BUSINESS_TRIP", "Командировка", True, True, False, None, False),
    ("REMOTE_WORK", "Удалённая работа", True, True, False, None, False),
    ("TRAINING", "Обучение", True, True, False, None, False),
)


def seed_permissions(session: Session) -> int:
    """Добавляет недостающие разрешения, существующие не трогает."""
    existing = {
        code for (code,) in session.execute(select(Permission.code)).all()
    }
    added = 0
    for code, name, description in PERMISSIONS:
        if code in existing:
            continue
        session.add(Permission(code=code, name=name, description=description))
        added += 1
    session.flush()
    return added


def seed_system_roles(session: Session) -> int:
    """Создаёт системные роли (organization_id IS NULL) и их разрешения."""
    permission_ids = {
        code: pid
        for pid, code in session.execute(
            select(Permission.id, Permission.code)
        ).all()
    }

    added = 0
    for code, name, description in SYSTEM_ROLES:
        role = session.scalar(
            select(Role).where(Role.code == code, Role.organization_id.is_(None))
        )
        if role is None:
            role = Role(
                code=code, name=name, description=description, is_system=True
            )
            session.add(role)
            session.flush()
            added += 1

        already = {
            pid
            for (pid,) in session.execute(
                select(RolePermission.permission_id).where(
                    RolePermission.role_id == role.id
                )
            ).all()
        }
        for permission_code in ROLE_PERMISSIONS.get(code, ()):
            pid = permission_ids.get(permission_code)
            if pid is None:
                raise RuntimeError(
                    f"Роль {code} ссылается на неизвестное разрешение {permission_code}"
                )
            if pid not in already:
                session.add(RolePermission(role_id=role.id, permission_id=pid))
    session.flush()
    return added


def seed_absence_types(session: Session, organization_id: uuid.UUID) -> int:
    """Типы отсутствий для одной организации."""
    existing = {
        code
        for (code,) in session.execute(
            select(AbsenceType.code).where(
                AbsenceType.organization_id == organization_id
            )
        ).all()
    }
    added = 0
    for (
        code, name, is_paid, requires_approval,
        requires_document, doc_after_days, deducts,
    ) in DEFAULT_ABSENCE_TYPES:
        if code in existing:
            continue
        session.add(
            AbsenceType(
                organization_id=organization_id,
                code=code,
                name=name,
                is_paid=is_paid,
                requires_approval=requires_approval,
                requires_document=requires_document,
                document_required_after_days=doc_after_days,
                deducts_leave_balance=deducts,
                is_active=True,
            )
        )
        added += 1
    session.flush()
    return added


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Наполнение справочников HUMOTECH")
    parser.add_argument("--organization-code", help="код существующей организации")
    parser.add_argument("--create-organization", help="создать организацию с этим кодом")
    parser.add_argument("--name", default=None, help="название новой организации")
    parser.add_argument("--timezone", default="Asia/Dushanbe", help="часовой пояс")
    args = parser.parse_args(argv)

    with session_scope() as session:
        print(f"разрешений добавлено: {seed_permissions(session)}")
        print(f"системных ролей добавлено: {seed_system_roles(session)}")

        org: Organization | None = None
        if args.create_organization:
            org = session.scalar(
                select(Organization).where(
                    Organization.code == args.create_organization
                )
            )
            if org is None:
                org = Organization(
                    code=args.create_organization,
                    name=args.name or args.create_organization,
                    default_timezone=args.timezone,
                    status="ACTIVE",
                )
                session.add(org)
                session.flush()
                print(f"организация создана: {org.code}")
        elif args.organization_code:
            org = session.scalar(
                select(Organization).where(
                    Organization.code == args.organization_code
                )
            )
            if org is None:
                print(
                    f"организация {args.organization_code} не найдена",
                    file=sys.stderr,
                )
                return 1

        if org is not None:
            print(f"типов отсутствий добавлено: {seed_absence_types(session, org.id)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
