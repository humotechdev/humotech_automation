"""Витрина HUMOTECH: состав, правила подтверждения и повторный запуск.

Проверяется то, из-за чего показ директору стал бы враньём:

— неподтверждённый больничный попал в табель;
— подтверждённый больничный без справки, заявления или дат HR;
— повторный запуск удвоил сотрудников или отметки;
— очистка задела чужие данные стенда;
— демо-привязка Telegram попала в диапазон живых адресатов.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.management import call_command

from humotech.absences.models import AbsenceRequest, EmployeeAbsence
from humotech.absences.services import approval_blockers, stage_of
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications.isolation import is_demo_chat_id
from humotech.onboarding.models import EmployeeOnboarding
from humotech.onboarding.services import seed_organization
from humotech.questions.models import EmployeeQuestion
from humotech.regions.models import Region
from humotech.surveys.models import SurveyCampaign
from humotech.telegram.models import TelegramAccount

pytestmark = pytest.mark.django_db(transaction=False)


@pytest.fixture()
def stand(organization, make_user):
    for code, name in (("TASHKENT_CITY", "город Ташкент"), ("SAMARKAND", "Самаркандская область"),
                       ("BUKHARA", "Бухарская область")):
        Region.objects.create(organization=organization, code=code, name=name, status="ACTIVE")
    make_user(organization)
    seed_organization(organization.id)
    return organization


def outsider(organization) -> Employee:
    """Настоящая карточка стенда: витрина не должна её трогать."""
    return Employee.objects.create(
        organization=organization, employee_number="HT-0001", first_name="Азизбек", last_name="Мурадов",
        hire_date=date(2025, 1, 1), employment_status="ACTIVE",
    )


def test_seed_builds_the_company_and_keeps_the_rules(stand):
    keep = outsider(stand)

    call_command("seed_demo_hr_data", "--code", stand.code)

    demo = Employee.objects.filter(organization=stand).exclude(id=keep.id)
    assert demo.count() == 200
    assert demo.filter(employment_status="ACTIVE").count() == 178
    assert demo.filter(employment_status="PROBATION").count() == 12
    assert demo.filter(employment_status="TERMINATED").count() == 10
    active = TelegramAccount.objects.filter(organization=stand, status="ACTIVE")
    assert active.count() == 165
    assert all(is_demo_chat_id(one) for one in active.values_list("telegram_chat_id", flat=True))

    # Подтверждённый больничный — только по полному комплекту.
    for request in AbsenceRequest.objects.filter(organization=stand, status="APPROVED"):
        assert approval_blockers(request) == ()
    stages = sorted(stage_of(one) for one in AbsenceRequest.objects.filter(
        organization=stand, absence_type__code="SICK_LEAVE").exclude(status="APPROVED"))
    assert stages == ["HR_REVIEW", "NEEDS_FIX", "WAITING_DOCUMENTS"]
    # Неподтверждённый больничный строки отсутствия не имеет.
    assert not EmployeeAbsence.objects.filter(origin_request__status__in=("SUBMITTED", "IN_REVIEW")).exists()
    # В дни подтверждённого отсутствия отметок нет.
    for absence in EmployeeAbsence.objects.filter(organization=stand, status="ACTIVE"):
        assert not AttendanceSession.objects.filter(
            employee=absence.employee, started_at__gte=absence.start_at, started_at__lt=absence.end_at).exists()

    assert EmployeeQuestion.objects.filter(organization=stand).count() == 14
    assert SurveyCampaign.objects.filter(organization=stand).count() == 4
    assert EmployeeOnboarding.objects.filter(organization=stand).count() == 190
    assert EmployeeAssignment.objects.filter(employee=keep).count() == 0


def test_second_run_gives_the_same_base_and_reset_removes_only_the_demo(stand):
    keep = outsider(stand)
    call_command("seed_demo_hr_data", "--code", stand.code)
    first = (Employee.objects.filter(organization=stand).count(), AttendanceEvent.objects.filter(organization=stand).count())

    call_command("seed_demo_hr_data", "--code", stand.code)
    second = (Employee.objects.filter(organization=stand).count(), AttendanceEvent.objects.filter(organization=stand).count())
    assert first == second

    call_command("seed_demo_hr_data", "--code", stand.code, "--reset-demo")
    assert list(Employee.objects.filter(organization=stand)) == [keep]
    assert not AttendanceEvent.objects.filter(organization=stand).exists()
