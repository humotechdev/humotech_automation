"""Увольнение, доступ и приостановка (ТЗ 12.10, решения В1, В2, В3, В5, В8, В10, Р-10).

* В1: при увольнении будущей датой сотрудник активен до конца последнего дня;
  статус меняет ежедневная задача.
* В2/В3: кадровые права учётки, привязанной к сотруднику, прекращаются —
  роли закрываются концом последнего дня (история сохраняется), сессии CRM
  отзываются.
* В8: нельзя уволить последнего действующего главного администратора.
* В10: граница — конец последнего дня по поясу организации; если пояс офиса
  другой, конфликт фиксируется, а не решается молча.
* Р-10: приостановка двух различимых видов (отстранение / отпуск без
  содержания); последствия видов — вопрос владельцу, не реализуются.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.contrib.sessions.models import Session
from django.test import Client

from django_tests.conftest import create_actor, make_office
from humotech.accounts.backends import OrganizationEmailBackend
from humotech.accounts.models import User, UserRoleScope
from humotech.audit.models import AuditLog
from humotech.core.errors import Conflict, ValidationFailed
from humotech.core.rbac import AccessControl
from humotech.core.timeframes import organization_zone, today as local_today
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.offboarding import apply_due_terminations
from humotech.employees.services import EmployeeService

pytestmark = pytest.mark.django_db

HR = ("employees.read", "employees.manage", "employees.archive")


@pytest.fixture()
def hr(make_actor, organization):
    return make_actor(organization, permissions=HR)


def _staff(organization, office, number, **extra):
    person = Employee.objects.create(
        organization=organization, employee_number=number, first_name="Анна",
        last_name="Иванова", hire_date=date(2024, 1, 1), employment_status="ACTIVE", **extra)
    EmployeeAssignment.objects.create(
        organization=organization, employee=person, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 1, 1))
    return person


def _account(organization, person, *, role="TEST_HR", permissions=("employees.read",),
             region=None, office=None):
    user, actor = create_actor(organization, permissions=permissions, role_code=role,
                               region=region, office=office)
    User.objects.filter(id=user.id).update(employee=person)
    return User.objects.get(id=user.id), actor


def _super_admin(organization, person=None, *, region=None):
    """Учётка с ролью SUPER_ADMIN — одна роль на организацию, как в бою."""
    from humotech.rbac.models import Role

    role, _ = Role.objects.get_or_create(organization=organization, code="SUPER_ADMIN",
                                         defaults={"name": "Суперадминистратор"})
    user, _ = create_actor(organization)
    UserRoleScope.objects.create(organization=organization, user=user, role=role,
                                 region=region,
                                 valid_from=datetime(2026, 1, 1, tzinfo=organization_zone(organization.id)))
    if person is not None:
        User.objects.filter(id=user.id).update(employee=person)
    return User.objects.get(id=user.id)


def _midnight_after(organization, day):
    tz = organization_zone(organization.id)
    return datetime.combine(day + timedelta(days=1), time(0, 5), tzinfo=tz)


class TestFutureTermination:
    def test_employee_stays_active_until_the_end_of_the_last_day(
        self, organization, office, hr
    ):
        person = _staff(organization, office, "T-1")
        last_day = local_today(organization_zone(organization.id)) + timedelta(days=5)
        EmployeeService().terminate(hr, person.id, termination_date=last_day)

        person.refresh_from_db()
        assert person.employment_status == "ACTIVE"
        assert person.termination_date == last_day
        assignment = EmployeeAssignment.objects.get(employee=person)
        assert assignment.valid_to == last_day

    def test_daily_job_terminates_after_the_boundary_and_closes_access(
        self, organization, office, hr
    ):
        person = _staff(organization, office, "T-2")
        user, actor = _account(organization, person)
        client = Client()
        client.force_login(user)
        last_day = local_today(organization_zone(organization.id)) + timedelta(days=2)
        EmployeeService().terminate(hr, person.id, termination_date=last_day)

        # В последний день, вечером — ещё работает.
        evening = datetime.combine(last_day, time(23, 0), tzinfo=organization_zone(organization.id))
        assert apply_due_terminations(now=evening) == 0
        person.refresh_from_db()
        assert person.employment_status == "ACTIVE"

        assert apply_due_terminations(now=_midnight_after(organization, last_day)) == 1
        person.refresh_from_db()
        assert person.employment_status == "TERMINATED"
        grant = UserRoleScope.objects.get(user=user)
        assert grant.valid_to is not None  # история роли сохранена, срок закрыт
        assert grant.valid_to <= _midnight_after(organization, last_day)
        assert not [s for s in Session.objects.all()
                    if str(s.get_decoded().get("_auth_user_id")) == str(user.id)]
        assert AuditLog.objects.filter(action="employee.termination.applied",
                                       entity_id=person.id).exists()
        # повторный запуск ничего не меняет
        assert apply_due_terminations(now=_midnight_after(organization, last_day)) == 0


class TestPastTermination:
    def test_past_date_terminates_now_and_closes_roles(self, organization, office, hr):
        person = _staff(organization, office, "T-3")
        user, actor = _account(organization, person, permissions=("employees.read",))
        EmployeeService().terminate(hr, person.id, termination_date=date(2026, 9, 1))
        person.refresh_from_db()
        assert person.employment_status == "TERMINATED"
        assert UserRoleScope.objects.get(user=user).valid_to is not None
        assert "employees.read" not in AccessControl().permissions(actor)

    def test_terminated_employee_account_has_no_session(self, organization, office, hr):
        person = _staff(organization, office, "T-4")
        user, _ = _account(organization, person)
        EmployeeService().terminate(hr, person.id, termination_date=date(2026, 9, 1))
        assert OrganizationEmailBackend().get_user(user.id) is None

    def test_technical_account_without_card_is_untouched(self, organization, office, hr):
        person = _staff(organization, office, "T-5")
        tech_user, tech_actor = create_actor(organization, permissions=("employees.read",))
        EmployeeService().terminate(hr, person.id, termination_date=date(2026, 9, 1))
        assert UserRoleScope.objects.get(user=tech_user).valid_to is None
        assert "employees.read" in AccessControl().permissions(tech_actor)


class TestLastSuperAdmin:
    def test_cannot_terminate_the_last_whole_organization_super_admin(
        self, organization, office, hr
    ):
        person = _staff(organization, office, "SA-1")
        _super_admin(organization, person)
        with pytest.raises(Conflict):
            EmployeeService().terminate(hr, person.id, termination_date=date(2026, 9, 1))

    def test_narrow_or_archived_super_admin_is_not_a_replacement(
        self, organization, region, office, hr
    ):
        person = _staff(organization, office, "SA-2")
        _super_admin(organization, person)
        _super_admin(organization, region=region)  # узкая область
        archived = _super_admin(organization)
        User.objects.filter(id=archived.id).update(archived_at=datetime(2026, 1, 1, tzinfo=organization_zone(organization.id)))
        with pytest.raises(Conflict):
            EmployeeService().terminate(hr, person.id, termination_date=date(2026, 9, 1))

    def test_replacement_allows_termination(self, organization, office, hr):
        person = _staff(organization, office, "SA-3")
        _super_admin(organization, person)
        _super_admin(organization)
        EmployeeService().terminate(hr, person.id, termination_date=date(2026, 9, 1))
        person.refresh_from_db()
        assert person.employment_status == "TERMINATED"

    def test_daily_job_rechecks_before_closing_access(self, organization, office, hr):
        """Замену назначили, но к дню увольнения её сняли — задача не оставит
        организацию без администратора: увольнение ждёт и попадает в журнал."""
        person = _staff(organization, office, "SA-4")
        _super_admin(organization, person)
        other = _super_admin(organization)
        last_day = local_today(organization_zone(organization.id)) + timedelta(days=1)
        EmployeeService().terminate(hr, person.id, termination_date=last_day)
        User.objects.filter(id=other.id).update(status="LOCKED")
        assert apply_due_terminations(now=_midnight_after(organization, last_day)) == 0
        person.refresh_from_db()
        assert person.employment_status == "ACTIVE"
        assert AuditLog.objects.filter(action="employee.termination.blocked",
                                       entity_id=person.id).exists()


def test_timezone_conflict_is_recorded_not_silently_resolved(organization, region, office, hr):
    remote = make_office(organization, region, "MSK")
    remote.timezone = "Europe/Moscow"
    remote.save(update_fields=["timezone"])
    person = _staff(organization, remote, "TZ-1")
    EmployeeService().terminate(hr, person.id, termination_date=date(2026, 9, 1))
    log = AuditLog.objects.get(action="employee.terminate", entity_id=person.id)
    assert log.new_values["timezone_conflict"] == {
        "organization": "Asia/Dushanbe", "office": "Europe/Moscow"}


class TestSuspensionKinds:
    def test_suspension_needs_a_kind(self, organization, office, hr):
        person = _staff(organization, office, "S-1")
        with pytest.raises(ValidationFailed):
            EmployeeService().deactivate(hr, person.id)

    @pytest.mark.parametrize("kind", ["REMOVED_FROM_WORK", "UNPAID_LEAVE"])
    def test_two_distinct_kinds_are_stored(self, organization, office, hr, kind):
        person = _staff(organization, office, f"S-{kind}")
        EmployeeService().deactivate(hr, person.id, kind=kind)
        person.refresh_from_db()
        assert person.employment_status == "SUSPENDED"
        assert person.suspension_kind == kind

    def test_reactivation_clears_the_kind(self, organization, office, hr):
        person = _staff(organization, office, "S-2")
        EmployeeService().deactivate(hr, person.id, kind="UNPAID_LEAVE")
        EmployeeService().reactivate(hr, person.id)
        person.refresh_from_db()
        assert person.employment_status == "ACTIVE" and person.suspension_kind is None
