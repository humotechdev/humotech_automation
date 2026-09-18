"""Наполнение справочников. Команда идемпотентна — можно запускать повторно.

Без неё система на пустой базе неработоспособна: `permissions` и `roles`
пусты, значит у любого пользователя набор разрешений пуст, и КАЖДЫЙ вызов
сервиса заканчивается отказом. Это не «нет данных», а «ничего не работает»,
поэтому команда — часть развёртывания, а не удобство.

Что делает:
  * `permissions`      — каталог разрешений (общий для всех организаций);
  * `roles`            — семь системных ролей (organization_id IS NULL);
  * `role_permissions` — привязка разрешений к ролям;
  * `absence_types`    — типы отсутствий, но уже ДЛЯ КОНКРЕТНОЙ организации,
                         потому что у таблицы organization_id NOT NULL.

Запуск:
    python manage.py seed                            # только общие справочники
    python manage.py seed --organization-code HUMO   # плюс типы отсутствий
    python manage.py seed --create-organization HUMO --name "HUMOTECH"
"""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from humotech.absences.models import AbsenceType
from humotech.core.permissions_catalog import (
    PERMISSIONS,
    ROLE_PERMISSIONS,
    SYSTEM_ROLES,
)
from humotech.organizations.models import Organization
from humotech.rbac.models import Permission, Role, RolePermission
from humotech.regions.models import Region

#: Часовой пояс страны. Один на всё: Узбекистан живёт в UTC+5 целиком,
#: и выбор пояса у офиса, региона или графика был бы выбором без вариантов.
COUNTRY_TIMEZONE = "Asia/Tashkent"

#: Области Узбекистана. Это география, а не данные клиента: список
#: закрыт, новых областей не появляется, и заводить их руками означало бы
#: получить «Ташкенская» рядом с «Ташкентской».
#:
#: Город Ташкент стоит рядом с областями: по устройству страны это
#: отдельная единица, но офис в нём завести надо, и выбирать его
#: кадровик должен там же, где остальные.
DEFAULT_REGIONS: tuple[tuple[str, str], ...] = (
    ("TASHKENT_CITY", "город Ташкент"),
    ("ANDIJAN", "Андижанская область"),
    ("BUKHARA", "Бухарская область"),
    ("FERGANA", "Ферганская область"),
    ("JIZZAKH", "Джизакская область"),
    ("KASHKADARYA", "Кашкадарьинская область"),
    ("KHOREZM", "Хорезмская область"),
    ("NAMANGAN", "Наманганская область"),
    ("NAVOIY", "Навоийская область"),
    ("SAMARKAND", "Самаркандская область"),
    ("SIRDARYO", "Сырдарьинская область"),
    ("SURKHANDARYA", "Сурхандарьинская область"),
    ("TASHKENT", "Ташкентская область"),
)

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


def seed_permissions() -> int:
    """Добавляет недостающие разрешения, существующие не трогает."""
    added = 0
    for code, name, description in PERMISSIONS:
        _, created = Permission.objects.get_or_create(
            code=code, defaults={"name": name, "description": description}
        )
        added += int(created)
    return added


def seed_system_roles() -> int:
    """Создаёт системные роли (organization_id IS NULL) и их разрешения."""
    permission_ids = dict(Permission.objects.values_list("code", "id"))
    added = 0

    for code, name, description in SYSTEM_ROLES:
        role, created = Role.objects.get_or_create(
            code=code,
            organization__isnull=True,
            defaults={"name": name, "description": description, "is_system": True},
        )
        added += int(created)

        already = set(
            RolePermission.objects.filter(role=role).values_list(
                "permission_id", flat=True
            )
        )
        for permission_code in ROLE_PERMISSIONS.get(code, ()):
            permission_id = permission_ids.get(permission_code)
            if permission_id is None:
                raise CommandError(
                    f"Роль {code} ссылается на неизвестное разрешение "
                    f"{permission_code}"
                )
            if permission_id not in already:
                RolePermission.objects.create(
                    role=role, permission_id=permission_id
                )
    return added


def seed_absence_types(organization_id: uuid.UUID) -> int:
    """Типы отсутствий для одной организации."""
    added = 0
    for (
        code, name, is_paid, requires_approval,
        requires_document, doc_after_days, deducts,
    ) in DEFAULT_ABSENCE_TYPES:
        _, created = AbsenceType.objects.get_or_create(
            organization_id=organization_id,
            code=code,
            defaults={
                "name": name,
                "is_paid": is_paid,
                "requires_approval": requires_approval,
                "requires_document": requires_document,
                "document_required_after_days": doc_after_days,
                "deducts_leave_balance": deducts,
                "is_active": True,
            },
        )
        added += int(created)
    return added


def seed_regions(organization_id: uuid.UUID) -> int:
    """Области Узбекистана для одной организации.

    Идемпотентна и ничего не переименовывает: если область уже заведена
    под другим названием, это решение организации, а не ошибка.
    """
    added = 0
    for code, name in DEFAULT_REGIONS:
        _, created = Region.objects.get_or_create(
            organization_id=organization_id,
            code=code,
            defaults={
                "name": name,
                # Пояс не задаём: он один на страну и берётся у организации.
                "timezone": None,
                "status": "ACTIVE",
            },
        )
        added += int(created)
    return added


class Command(BaseCommand):
    help = "Наполняет справочники разрешений, ролей, областей и типов отсутствий"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--organization-code",
                            help="код существующей организации")
        parser.add_argument("--create-organization",
                            help="создать организацию с этим кодом")
        parser.add_argument("--name", default=None,
                            help="название новой организации")
        parser.add_argument("--timezone", default=COUNTRY_TIMEZONE,
                            help="часовой пояс новой организации")
        parser.add_argument("--only", choices=["regions", "absence-types"],
                            help="наполнить только один справочник организации")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        self.stdout.write(f"разрешений добавлено: {seed_permissions()}")
        self.stdout.write(f"системных ролей добавлено: {seed_system_roles()}")

        organization = None
        if options["create_organization"]:
            code = options["create_organization"]
            organization, created = Organization.objects.get_or_create(
                code=code,
                defaults={
                    "name": options["name"] or code,
                    "default_timezone": options["timezone"],
                    "status": "ACTIVE",
                },
            )
            if created:
                self.stdout.write(f"организация создана: {organization.code}")
        elif options["organization_code"]:
            organization = Organization.objects.filter(
                code=options["organization_code"]
            ).first()
            if organization is None:
                raise CommandError(
                    f"организация {options['organization_code']} не найдена"
                )

        if organization is not None:
            only = options.get("only")
            if only in (None, "regions"):
                self.stdout.write(
                    f"областей добавлено: {seed_regions(organization.id)}"
                )
            if only in (None, "absence-types"):
                self.stdout.write(
                    f"типов отсутствий добавлено: "
                    f"{seed_absence_types(organization.id)}"
                )
