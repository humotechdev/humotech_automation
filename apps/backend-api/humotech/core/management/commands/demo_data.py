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

# Разрешения, которые демонстрационная роль обязана иметь СВЕРХ уже
# согласованного набора, чтобы работали готовые разделы CRM.
#
# Список короткий и явный намеренно. Он добавляется УЖЕ СУЩЕСТВУЮЩЕЙ роли
# на живом стенде, поэтому каждая новая строка здесь — это расширение
# доступа у всех, кто обновится. Кадровые разрешения такому расширению
# подлежат, управление доступом — нет.
DEMO_ROLE_REQUIRED = ("notifications.read", "notifications.manage")

# Разрешения, которые этой командой не выдаются никогда. Проверка стоит
# не ради красоты: правка списка выше — это одна строка, и без неё
# «заодно добавить audit.read» выглядело бы безобидно.
NEVER_GRANTED_BY_DEMO = (
    "users.manage", "roles.manage", "settings.manage", "audit.read",
)

_overreach = set(DEMO_ROLE_REQUIRED) & set(NEVER_GRANTED_BY_DEMO)
if _overreach:
    raise AssertionError(
        "demo_data не выдаёт административные разрешения существующей "
        f"роли: {sorted(_overreach)}"
    )


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
            "--add-employee", metavar="ФАМИЛИЯ ИМЯ",
            help="добавить ещё одного сотрудника в тот же офис и график; "
                 "табельный номер выдаётся следующий по порядку",
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

        if options["add_employee"]:
            extra = self._add_employee(
                org, office, department, position, schedule,
                options["add_employee"], annual,
            )
            self.stdout.write(self.style.SUCCESS(
                f"Добавлен сотрудник: {extra.last_name} {extra.first_name}, "
                f"таб. № {extra.employee_number}"
            ))
            self.stdout.write(
                "  Ссылка привязки:  manage.py telegram_link "
                f"--issue {extra.employee_number} --as-user {options['hr_email']}"
            )
            return

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

    def _add_employee(
        self, org, office, department, position, schedule, full_name, annual,
    ):
        """Ещё один сотрудник в том же офисе — чтобы проверять вдвоём.

        Номер выдаётся следующий свободный: придумывать его руками значит
        рано или поздно занять чужой.
        """
        parts = full_name.split()
        last_name = parts[0]
        first_name = parts[1] if len(parts) > 1 else "Сотрудник"

        taken = set(
            Employee.objects.filter(
                organization=org, employee_number__startswith=f"{org.code}-"
            ).values_list("employee_number", flat=True)
        )
        number = next(
            f"{org.code}-{n:03d}" for n in range(1, 1000)
            if f"{org.code}-{n:03d}" not in taken
        )

        employee = Employee.objects.create(
            organization=org,
            employee_number=number,
            first_name=first_name,
            last_name=last_name,
            hire_date=date.today(),
            employment_status="ACTIVE",
            preferred_language="ru",
        )
        EmployeeAssignment.objects.create(
            organization=org, employee=employee, office=office,
            department=department, position=position,
            employment_type="FULL_TIME", work_mode="ONSITE",
            is_primary=True, valid_from=employee.hire_date,
        )
        EmployeeScheduleAssignment.objects.create(
            organization=org, employee=employee, schedule=schedule,
            valid_from=employee.hire_date,
        )
        if annual is not None:
            LeaveBalance.objects.create(
                organization=org, employee=employee, absence_type=annual,
                year=date.today().year,
                allocated_minutes=ANNUAL_LEAVE_DAYS * MINUTES_PER_WORKING_DAY,
            )
        return employee

    def _demo_role_permissions(self, role, created: bool) -> None:
        """Права демонстрационной роли — воспроизводимо, а не «при создании».

        Раньше набор раздавался только внутри `if created`. Роль, заведённая
        однажды, больше не получала ничего: разрешение, появившееся в
        каталоге позже, на стенде просто отсутствовало, и раздел CRM,
        который на него опирался, не открывался. Чинилось это разовой
        вставкой в базу — то есть шагом, о котором знал один человек.

        Теперь у команды два разных поведения, и разница принципиальная:

          * НОВОЙ роли выдаётся весь набор HR_ADMIN. Роли ещё нет, решать
            нечего;
          * СУЩЕСТВУЮЩЕЙ роли добавляются только те разрешения, которые
            нужны уже готовым разделам стенда, — короткий явный список.
            Синхронизировать её с каталогом целиком нельзя: это тихо
            повысило бы права на всех стендах разом, а «стенд обновили»
            не значит «решили выдать больше доступа».

        Права ни у одной другой роли команда не трогает.
        """
        from humotech.core.permissions_catalog import ROLE_PERMISSIONS
        from humotech.rbac.models import Permission, RolePermission

        codes = (
            tuple(ROLE_PERMISSIONS.get("HR_ADMIN", ()))
            if created
            else DEMO_ROLE_REQUIRED
        )
        found = {
            permission.code: permission
            for permission in Permission.objects.filter(code__in=codes)
        }
        missing = [code for code in codes if code not in found]
        if missing:
            # Молчать нельзя: разрешения нет в каталоге, роль его не
            # получит, и раздел не откроется — но выглядеть это будет
            # как успешный запуск.
            self.stdout.write(self.style.WARNING(
                "В каталоге разрешений нет: " + ", ".join(sorted(missing))
                + ". Сначала `manage.py seed`."
            ))

        added = 0
        for permission in found.values():
            _, made = RolePermission.objects.get_or_create(
                role=role, permission=permission
            )
            added += int(made)
        if added:
            self.stdout.write(f"  прав добавлено роли {role.code}: {added}")

    def _hr_user(self, org, options) -> str | None:
        """Кадровик с полными правами по своей организации."""
        role, created = Role.objects.get_or_create(
            organization=org, code="HR_ADMIN_LOCAL",
            defaults={"name": "Кадровик организации"},
        )
        self._demo_role_permissions(role, created)

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
