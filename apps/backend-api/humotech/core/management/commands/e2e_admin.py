"""Учётка для автоматической браузерной проверки изолированного стенда.

Зачем отдельная команда. Снимки страниц делает headless-браузер, а он
стартует без сессии и упирается в форму входа. Заводить ради этого
человека в рабочей базе нельзя, а `demo_data` кроме учётки создаёт ещё
офис и сотрудника — состав витрины после него перестал бы сходиться.

Что делает. Заводит ОДНОГО пользователя с ролью только на чтение и
ничего больше. Повторный запуск ту же учётку переиспользует.

Где работает. Только на базе стенда: имя вида `test_*` или `*_e2e`.
На любой другой команда отказывается — синтетический администратор в
рабочей базе это чужой доступ, а не удобство проверки.

Пароль. Печатается ТОЛЬКО в файл, путь к которому передан явно. В
журнал, в вывод команды и в историю оболочки он не попадает: эти три
места читают все, кому не нужно.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from humotech.accounts.models import User, UserRoleScope
from humotech.organizations.models import Organization
from humotech.rbac.models import Permission, Role, RolePermission

#: Имена баз, на которых команда согласна работать.
STAND_DATABASE = re.compile(r"^test_|_e2e$")

#: Что этой учётке позволено. Только чтение: она открывает страницы и
#: снимает их, а не меняет данные.
PERMISSIONS = (
    "employees.read",
    "attendance.read",
    "absences.read",
    "analytics.read",
    "offices.read",
    "reports.read",
    "questions.read",
    "notifications.read",
    "knowledge.read",
)


class Command(BaseCommand):
    help = "Заводит учётку для браузерной проверки на изолированном стенде"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--code", required=True, help="код организации")
        parser.add_argument(
            "--email", default="shots@humotech.e2e",
            help="адрес учётки; повторный запуск её переиспользует",
        )
        parser.add_argument(
            "--out", required=True,
            help="файл, в который записать адрес и пароль. Больше никуда "
                 "они не выводятся.",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        self._guard()

        org = Organization.objects.filter(code=options["code"]).first()
        if org is None:
            raise CommandError(f"Организация {options['code']} не найдена")

        role = self._role(org)
        user = User.objects.filter(organization=org, email=options["email"]).first()
        if user is None:
            user = User(organization=org, email=options["email"], status="ACTIVE")
        # Пароль задаётся заново при каждом запуске: старый нигде не
        # хранится, и восстановить его нечем.
        password = secrets.token_urlsafe(18)
        user.set_password(password)
        user.save()

        UserRoleScope.objects.get_or_create(
            organization=org, user=user, role=role, region=None, office=None,
            defaults={"valid_from": timezone.now() - timedelta(days=1)},
        )

        out = Path(options["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            f"HUMOTECH_EMAIL={options['email']}\nHUMOTECH_PASSWORD={password}\n",
            encoding="utf-8",
        )
        try:
            os.chmod(out, 0o600)
        except OSError:  # pragma: no cover - файловая система без прав
            pass

        self.stdout.write(self.style.SUCCESS(
            f"Учётка для снимков готова, данные записаны в {out}. "
            "В выводе команды их нет намеренно."
        ))

    @staticmethod
    def _guard() -> None:
        module = getattr(settings, "SETTINGS_MODULE", "") or ""
        if module == "config.settings.production":
            raise CommandError(
                "Боевые настройки. Учётка для снимков в рабочей базе не "
                "заводится ни при каких ключах."
            )
        name = settings.DATABASES["default"].get("NAME") or ""
        if not STAND_DATABASE.search(name):
            raise CommandError(
                f"База «{name}» не похожа на стенд. Команда работает только "
                "на базах вида test_* или *_e2e: синтетический администратор "
                "в рабочей базе — это чужой доступ, а не удобство проверки."
            )

    def _role(self, org) -> Role:
        role, _ = Role.objects.get_or_create(
            organization=org, code="E2E_SHOTS",
            defaults={"name": "Браузерная проверка (только чтение)"},
        )
        for code in PERMISSIONS:
            permission = Permission.objects.filter(code=code).first()
            if permission is not None:
                RolePermission.objects.get_or_create(role=role, permission=permission)
        return role
