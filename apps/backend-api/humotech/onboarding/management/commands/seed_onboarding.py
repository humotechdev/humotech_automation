"""Стартовые карточки и документы ознакомления для организации.

Миграция наполняет только те компании, что существовали в день её
применения. Новой организации наполнение ставит эта команда — и она же
чинит стенд, на котором раздел выглядит пустым.

Идемпотентна: повторный запуск ничего не дублирует и не переписывает
уже поправленные кадровиком тексты.

    manage.py seed_onboarding                 # все организации
    manage.py seed_onboarding --organization <UUID>
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from humotech.onboarding.services import seed_organization
from humotech.organizations.models import Organization


class Command(BaseCommand):
    help = "Создать программу ознакомления и обязательные документы"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--organization",
            help="UUID организации. Без него — все организации.",
        )

    def handle(self, *args, **options) -> None:
        wanted = options.get("organization")
        rows = Organization.objects.all()
        if wanted:
            rows = rows.filter(id=wanted)
            if not rows.exists():
                raise CommandError(f"Организация {wanted} не найдена")

        for organization in rows:
            made = seed_organization(organization.id)
            self.stdout.write(
                f"{organization.name}: программа {made['program']}, "
                f"карточек +{made['sections']}, документов +{made['documents']}, "
                f"редакций +{made['versions']}"
            )
