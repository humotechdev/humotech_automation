"""Рабочий комплект данных для локальной проверки сотруднической части.

Отличие от `seed`: тот наполняет справочники, без которых система вообще
не работает — разрешения, роли, типы отсутствий. Этот заводит ПРИМЕР:
организацию, офис, точку QR, сотрудника, график, кадровика. Такие данные
в бою не нужны и вредны, поэтому команда отказывается работать там, где
`DEBUG` выключен, если не сказать `--yes` явно.

Повторный запуск ничего не ломает: всё заводится через `get_or_create`
по коду, поэтому команду можно звать сколько угодно раз.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from humotech.absences.models import AbsenceType, LeaveBalance
from humotech.accounts.models import User, UserRoleScope
from humotech.departments.models import Department
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.offices.models import Office
from humotech.organizations.models import Organization
from humotech.positions.models import Position
from humotech.qr_codes.models import OfficeQrPoint
from humotech.rbac.models import Role
from humotech.regions.models import Region
from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)

MINUTES_PER_WORKING_DAY = 480
ANNUAL_LEAVE_DAYS = 28


class Command(BaseCommand):
    help = "Заводит пример организации, офиса, точки QR, сотрудника и кадровика"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--code", default="DEMO", help="код организации")
        parser.add_argument("--name", default="HUMOTECH Демо")
        parser.add_argument("--timezone", default="Asia/Dushanbe")
        parser.add_argument("--hr-email", default="hr@humotech.local")
        parser.add_argument(
            "--hr-password",
            help="пароль кадровика; без него пароль не меняется у существующего "
                 "и генерируется у нового",
        )
        parser.add_argument(
            "--yes", action="store_true",
            help="подтвердить запуск при выключенном DEBUG",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        if not settings.DEBUG and not options["yes"]:
            raise CommandError(
                "DEBUG выключен. Пример данных в рабочей базе не нужен; "
                "если это всё-таки то, что нужно, повторите с --yes."
            )

        tz = options["timezone"]
        code = options["code"]
        today = date.today()

        org, _ = Organization.objects.get_or_create(
            code=code,
            defaults={
                "name": options["name"],
                "default_timezone": tz,
                "status": "ACTIVE",
            },
        )
        region, _ = Region.objects.get_or_create(
            organization=org, code=f"{code}-C",
            defaults={"name": "Центральный регион", "status": "ACTIVE"},
        )
        office, _ = Office.objects.get_or_create(
            organization=org, code=f"{code}-HQ",
            defaults={
                "region": region,
                "name": "Головной офис",
                "address": "г. Душанбе, пр. Рудаки, 1",
                "timezone": tz,
                "status": "ACTIVE",
            },
        )
        department, _ = Department.objects.get_or_create(
            organization=org, code=f"{code}-IT",
            defaults={"office": office, "name": "Отдел разработки",
                      "status": "ACTIVE"},
        )
        position, _ = Position.objects.get_or_create(
            organization=org, code=f"{code}-DEV",
            defaults={"name": "Инженер-программист", "status": "ACTIVE"},
        )

        # Точка BOTH: одна и та же на вход и на выход. Для проверки это
        # удобнее двух точек — сканировать можно один экран.
        point, _ = OfficeQrPoint.objects.get_or_create(
            organization=org, office=office, code=f"{code}-DOOR",
            defaults={
                "name": "Главный вход",
                "direction_mode": "BOTH",
                "qr_mode": "ROTATING",
                "rotation_seconds": 30,
            },
        )

        schedule, created = WorkSchedule.objects.get_or_create(
            organization=org, name="Пятидневка 09:00–18:00",
            defaults={"timezone": tz, "weekly_minutes": 2400, "status": "ACTIVE"},
        )
        if created:
            for weekday in range(1, 8):
                working = weekday <= 5
                ScheduleDay.objects.create(
                    schedule=schedule,
                    weekday=weekday,
                    is_working_day=working,
                    start_time="09:00" if working else None,
                    end_time="18:00" if working else None,
                )

        employee, _ = Employee.objects.get_or_create(
            organization=org, employee_number=f"{code}-001",
            defaults={
                "first_name": "Далер",
                "last_name": "Рахимов",
                "hire_date": today - timedelta(days=400),
                "employment_status": "ACTIVE",
                "preferred_language": "ru",
            },
        )
        EmployeeAssignment.objects.get_or_create(
            organization=org, employee=employee, is_primary=True,
            defaults={
                "office": office,
                "department": department,
                "position": position,
                "employment_type": "FULL_TIME",
                "work_mode": "ONSITE",
                "valid_from": employee.hire_date,
            },
        )
        EmployeeScheduleAssignment.objects.get_or_create(
            organization=org, employee=employee, schedule=schedule,
            defaults={"valid_from": employee.hire_date},
        )

        # Типы отсутствий заводит `seed --organization-code`; здесь только
        # остаток, и только если тип отпуска уже есть.
        annual = AbsenceType.objects.filter(
            organization=org, code="ANNUAL_LEAVE"
        ).first()
        if annual is not None:
            LeaveBalance.objects.get_or_create(
                organization=org, employee=employee, absence_type=annual,
                year=today.year,
                defaults={
                    "allocated_minutes": ANNUAL_LEAVE_DAYS
                    * MINUTES_PER_WORKING_DAY
                },
            )

        hr_password = self._hr_user(org, options)

        self.stdout.write(self.style.SUCCESS("Пример данных готов."))
        self.stdout.write(f"  организация:  {org.name} ({org.code})")
        self.stdout.write(f"  офис:         {office.name}, пояс {office.timezone}")
        self.stdout.write(f"  точка QR:     {point.name} ({point.code}, "
                          f"{point.direction_mode})")
        self.stdout.write(f"  сотрудник:    {employee.last_name} "
                          f"{employee.first_name}, таб. № "
                          f"{employee.employee_number}")
        self.stdout.write(f"  график:       {schedule.name}")
        if annual is None:
            self.stdout.write(self.style.WARNING(
                "  остаток отпуска НЕ заведён: сначала "
                f"`manage.py seed --organization-code {org.code}`"
            ))
        self.stdout.write(f"  кадровик:     {options['hr_email']}")
        if hr_password:
            self.stdout.write(f"  пароль:       {hr_password}")

    def _hr_user(self, org, options) -> str | None:
        """Кадровик с полными правами по своей организации."""
        role, created = Role.objects.get_or_create(
            organization=org, code="HR_ADMIN_LOCAL",
            defaults={"name": "Кадровик организации"},
        )
        if created:
            from humotech.core.permissions_catalog import ROLE_PERMISSIONS
            from humotech.rbac.models import Permission, RolePermission

            codes = ROLE_PERMISSIONS.get("HR_ADMIN", ())
            for permission in Permission.objects.filter(code__in=codes):
                RolePermission.objects.get_or_create(
                    role=role, permission=permission
                )

        user = User.objects.filter(
            organization=org, email=options["hr_email"]
        ).first()
        shown = None
        if user is None:
            import secrets

            shown = options["hr_password"] or secrets.token_urlsafe(12)
            user = User(
                organization=org, email=options["hr_email"], status="ACTIVE"
            )
            user.set_password(shown)
            user.save()
        elif options["hr_password"]:
            shown = options["hr_password"]
            user.set_password(shown)
            user.save(update_fields=["password_hash", "updated_at"])

        UserRoleScope.objects.get_or_create(
            organization=org, user=user, role=role,
            region=None, office=None,
            defaults={"valid_from": timezone.now() - timedelta(days=1)},
        )
        return shown
