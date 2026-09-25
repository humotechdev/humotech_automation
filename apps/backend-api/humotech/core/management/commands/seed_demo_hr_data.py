"""Демонстрационная компания HUMOTECH: двести сотрудников и месяц работы.

    python manage.py seed_demo_hr_data              # завести (или пересобрать) витрину
    python manage.py seed_demo_hr_data --reset-demo # только убрать витрину

Что заводится: четыре офиса, девять отделов с руководителями, 200
карточек (178 штатных, 12 стажёров, 10 бывших), графики, Telegram,
история карточек, отметки за 30 дней и сегодняшний день, заявки
(отпуска, больничные по всем правилам подтверждения, командировки,
исправления отметок), 14 обращений с перепиской и статьями базы
знаний, опросы с ответами и автоматизациями, разделы и материалы
ознакомления с назначениями и подтверждениями, стажировки с
наставниками.

**Готовых чисел нет.** Дашборд, аналитика и отчёты считают всё по
записанным событиям теми же правилами, что и в работе.

**Повтор не размножает.** У каждой строки витрины постоянный ключ от
её имени. Запуск сначала убирает свою прошлую витрину, потом заводит
заново: результат тот же, вплоть до минут в отметках (относительно
текущего дня).

**Чужое не трогается.** Удаляются только строки витрины и то, что без
них не может существовать. Карточки HT-0001…HT-0100 и прочие данные
стенда остаются как есть.

**В бою не работает.** Отказ на `config.settings.production` и на базе,
имя которой не похоже на стенд. На стенде команда запускается с
`DJANGO_SETTINGS_MODULE=config.settings.e2e`.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from humotech.accounts.models import User
from humotech.audit.models import AuditLog
from humotech.core.demo.hr import attendance, inbox, onboarding, people as people_module, requests, surveys
from humotech.core.demo.hr import catalog as cat
from humotech.core.demo.hr.people import did
from humotech.core.demo.hr.purge import purge
from humotech.absences.models import AbsenceType
from humotech.departments.models import Department
from humotech.employees.models import Employee
from humotech.files.models import File
from humotech.files.storage import private_storage
from humotech.knowledge.models import KnowledgeSource
from humotech.offices.models import Office
from humotech.onboarding.models import PolicyCategory, PolicyDocument
from humotech.organizations.models import Organization
from humotech.positions.models import Position
from humotech.qr_codes.models import OfficeQrPoint
from humotech.schedules.models import WorkSchedule
from humotech.surveys.models import SurveyAutomation, SurveyCampaign, SurveyTemplate

FORBIDDEN_SETTINGS = "config.settings.production"
#: База витрины: стенд, тесты или разработка. Неизвестное имя — рабочее.
ALLOWED_DATABASE = re.compile(r"(^test_|_e2e$|^humotech$|_dev$|_demo$)")


class Command(BaseCommand):
    help = "Демонстрационная компания HUMOTECH: 200 сотрудников и месяц работы"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--code", default="DEMO", help="код организации (по умолчанию DEMO)")
        parser.add_argument("--reset-demo", action="store_true", help="только удалить витрину и выйти")

    def handle(self, *args, **options) -> None:
        self._guard()
        org = Organization.objects.filter(code=options["code"]).first()
        if org is None:
            raise CommandError(f"Организация {options['code']} не найдена")
        tz = ZoneInfo(org.default_timezone)
        now = timezone.now().astimezone(tz)
        today = now.date()

        with transaction.atomic():
            removed = self._reset(org)
            self.stdout.write(f"Прежняя витрина убрана: {removed} строк")
            if options["reset_demo"]:
                return
            reviewer = self._reviewer(org)
            report = self._seed(org, today, now, tz, reviewer)
        for line in report:
            self.stdout.write(line)

    # --- защита -------------------------------------------------------------

    def _guard(self) -> None:
        module = getattr(settings, "SETTINGS_MODULE", "") or ""
        if module == FORBIDDEN_SETTINGS:
            raise CommandError(
                "Боевые настройки. Демонстрационные данные в рабочей базе не заводятся. "
                "На стенде: DJANGO_SETTINGS_MODULE=config.settings.e2e"
            )
        name = connection.settings_dict.get("NAME") or ""
        if not ALLOWED_DATABASE.search(name):
            raise CommandError(f"База «{name}» не похожа на стенд. Витрина заводится только на стенде.")
        root = private_storage().location
        self.stdout.write(f"База: {name} · файлы: {root} · настройки: {module}")

    # --- очистка --------------------------------------------------------------

    def _reset(self, org) -> int:
        removed = 0
        numbers = [cat.number(at) for at in range(cat.NUMBER_TO - cat.NUMBER_FROM + 1)]
        employees = Employee.objects.filter(organization=org, employee_number__in=numbers)
        ids = list(employees.values_list("id", flat=True))
        removed += AuditLog.objects.filter(organization=org, entity_id__in=ids).delete()[0]
        removed += purge(SurveyAutomation.objects.filter(organization=org, id__in=[did("automation", k) for k in ("adapt", "probation")]))
        removed += purge(SurveyCampaign.objects.filter(organization=org, id__in=[
            did("campaign", k) for k in ("satisfaction", "manager", "probation", "adapt-aug")]))
        removed += purge(SurveyTemplate.objects.filter(organization=org, id__in=[did("template", t[0]) for t in cat.TEMPLATES]))
        removed += purge(employees)
        removed += purge(KnowledgeSource.objects.filter(organization=org, meta__demo=cat.MARK))
        removed += purge(PolicyDocument.objects.filter(organization=org, code__startswith=f"{cat.MARK}-"))
        # Раздел обнуляет ссылку у существующих документов стенда — они остаются.
        removed += purge(PolicyCategory.objects.filter(organization=org, id__in=[did("category", c[0]) for c in cat.CATEGORIES]))
        removed += purge(OfficeQrPoint.objects.filter(organization=org, code__startswith=f"{cat.MARK}-"))
        removed += purge(Department.objects.filter(organization=org, code__startswith=f"{cat.MARK}-"))
        removed += purge(Position.objects.filter(organization=org, code__startswith=f"{cat.MARK}-"))
        removed += purge(WorkSchedule.objects.filter(organization=org, id__in=[did("schedule", s.code) for s in cat.SCHEDULES]))
        removed += purge(Office.objects.filter(organization=org, code__startswith=f"{cat.MARK}-"))
        removed += purge(AbsenceType.objects.filter(organization=org, id__in=[
            did("absence-type", code) for code in ("ANNUAL_LEAVE", "SICK_LEAVE", "BUSINESS_TRIP")]))
        files = File.objects.filter(organization=org, storage_key__startswith=f"{requests.FILE_PREFIX}/")
        storage = private_storage()
        for key in files.values_list("storage_key", flat=True):
            try:
                storage.delete(key)
            except OSError:
                pass
        removed += purge(files)
        return removed

    # --- посев ------------------------------------------------------------------

    def _seed(self, org, today, now, tz, reviewer) -> list[str]:
        structure = people_module.build_structure(org)
        staff = people_module.build_people(org, structure, today, reviewer, tz)
        types = requests.absence_types(org)
        absent, quiet, request_counts = requests.build(org, staff, types, today, now, tz, reviewer)
        marks = attendance.build(org, structure, staff, today, now, tz, absent | quiet)
        fixes = requests.corrections(org, staff, today, now, reviewer)
        questions = inbox.build(org, staff, now, reviewer)
        polls = surveys.build(org, structure, staff, today, now, tz, reviewer)
        learning = onboarding.build(org, staff, today, now, tz, reviewer)
        active = [p for p in staff if p.active]
        return [
            f"Сотрудники: {len(staff)} (активных {len(active)}, стажёров "
            f"{sum(p.status == 'PROBATION' for p in staff)}, бывших {sum(p.status == 'TERMINATED' for p in staff)})",
            f"Telegram: привязан у {sum(p.telegram == 'ACTIVE' for p in active)} активных",
            f"Отметки: {marks['events']} событий, {marks['sessions']} смен, {marks['notices']} предупреждений",
            f"Заявки: {request_counts['requests']} на отсутствия, {request_counts['absences']} подтверждённых, "
            f"{fixes} исправлений отметок",
            f"Обращения: {questions}",
            f"Опросы: {polls}",
            f"Ознакомления: {learning}",
        ]

    @staticmethod
    def _reviewer(org) -> User:
        """От чьего имени решения витрины: первый пользователь организации."""
        user = User.objects.filter(organization=org).order_by("created_at").first()
        if user is None:
            raise CommandError("В организации нет пользователей CRM — решения витрины некому подписать")
        return user
