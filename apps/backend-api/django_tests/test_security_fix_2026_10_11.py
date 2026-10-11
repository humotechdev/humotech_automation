"""Регрессии аудита безопасности 11.10.2026.

Общая первопричина большинства находок — проверка шла по объединённой
области всех назначений актора (`scope()`, `has()`, `permissions()`),
а не по области того назначения, что несёт нужное право
(`scope_for(actor, perm)`). Типовой обход — «комбо»: право на запись
в офисе A плюс «только чтение» в офисе B. Каждый тест ниже проверяет
и отказ, и то, что законный путь по-прежнему работает: чинили не
«запретить всё».
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from django_tests.conftest import create_actor, make_office
from humotech.accounts.models import UserRoleScope
from humotech.core.errors import ValidationFailed
from humotech.employees.attachments import EmployeeAttachmentService
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.rbac.models import Permission, Role, RolePermission

pytestmark = pytest.mark.django_db

API = "/api/v1"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF\n"


def _employee(organization, office, number, *, valid_from=date(2024, 1, 1),
              valid_to=None, status="ACTIVE"):
    emp = Employee.objects.create(
        organization=organization, employee_number=number,
        first_name="Тест", last_name="Аудит",
        hire_date=date(2024, 1, 1), employment_status=status,
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=emp, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=valid_from, valid_to=valid_to,
    )
    return emp


def _add_grant(organization, user, *, permissions, role_code,
               office=None, region=None):
    role = Role.objects.create(organization=organization, code=role_code,
                               name=role_code)
    for code in permissions:
        perm, _ = Permission.objects.get_or_create(
            code=code, defaults={"name": code, "description": code})
        RolePermission.objects.create(role=role, permission=perm)
    UserRoleScope.objects.create(
        organization=organization, user=user, role=role,
        office=office, region=region,
        valid_from=datetime.now(tz=dt_timezone.utc) - timedelta(days=1),
    )
    return role


def _combo(organization, region, *, write, read, tag):
    """Запись `write` в офисе A + чтение `read` в офисе B.

    Возвращает (user, actor, office_a, office_b)."""
    office_a = make_office(organization, region, f"{tag}-A")
    office_b = make_office(organization, region, f"{tag}-B")
    user, actor = create_actor(organization, permissions=write, office=office_a,
                               role_code=f"{tag}_WRITE_A")
    _add_grant(organization, user, permissions=read, office=office_b,
               role_code=f"{tag}_READ_B")
    return user, actor, office_a, office_b


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _pdf(name="pass.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


# =============================================================================
# H-1. Чужой файл по file_id
# =============================================================================


class TestForeignFileId:
    """Кадровик не привязывает к «своему» сотруднику чужой файл по UUID.

    Карточка отдаёт `documents[].file_id` всем с `employees.read`; раньше
    `take()` проверял только организацию — паспорт из офиса B уходил
    в документы сотрудника офиса A и скачивался там."""

    def _passport_in_b(self, organization, office_b):
        hr_b_user, hr_b = create_actor(
            organization, permissions=("employees.read", "employees.manage"),
            office=office_b)
        emp_b = _employee(organization, office_b, "H1-EB")
        service = EmployeeAttachmentService()
        record = service.upload(hr_b, upload=_pdf(), purpose="document")
        doc = service.attach_document(hr_b, emp_b.id, kind="IDENTITY",
                                      file_id=record.id, title="Паспорт")
        return emp_b, record, doc

    def test_combo_cannot_attach_foreign_passport(self, organization, region):
        user, _, office_a, office_b = _combo(
            organization, region,
            write=("employees.read", "employees.manage"),
            read=("employees.read",), tag="H1")
        emp_b, record, _ = self._passport_in_b(organization, office_b)
        emp_a = _employee(organization, office_a, "H1-EA")
        client = _client(user)

        # Сценарий атаки: id файла берётся из карточки сотрудника B…
        card = client.get(f"{API}/employees/{emp_b.id}/")
        assert card.status_code == 200
        leaked = card.json()["documents"][0]["file_id"]
        assert leaked == str(record.id)

        # …и прикрепляется к своему сотруднику A — теперь отказ.
        attach = client.post(f"{API}/employees/{emp_a.id}/documents/",
                             {"kind": "OTHER", "file_id": leaked}, format="json")
        assert attach.status_code == 400, attach.content
        photo = client.post(f"{API}/employees/{emp_a.id}/photo-set/",
                            {"file_id": leaked}, format="json")
        assert photo.status_code == 400, photo.content
        # Скачать паспорт B ни прямо, ни обходом нельзя.
        doc_id = card.json()["documents"][0]["id"]
        direct = client.get(f"{API}/employees/{emp_b.id}/documents/{doc_id}/download/")
        assert direct.status_code == 403
        assert b"%PDF" not in direct.content

    def test_same_org_wide_hr_cannot_reuse_attached_file(self, organization, office):
        """Даже кадровик всей организации не дублирует уже привязанный файл:
        файл попадает в карточку одним путём — своей загрузкой."""
        user, hr = create_actor(organization,
                                permissions=("employees.read", "employees.manage"))
        emp_1 = _employee(organization, office, "H1-1")
        emp_2 = _employee(organization, office, "H1-2")
        service = EmployeeAttachmentService()
        record = service.upload(hr, upload=_pdf(), purpose="document")
        service.attach_document(hr, emp_1.id, kind="IDENTITY", file_id=record.id)
        with pytest.raises(ValidationFailed):
            service.attach_document(hr, emp_2.id, kind="OTHER", file_id=record.id)

    def test_other_users_fresh_upload_is_refused(self, organization, office):
        _, hr_1 = create_actor(organization,
                               permissions=("employees.read", "employees.manage"))
        _, hr_2 = create_actor(organization,
                               permissions=("employees.read", "employees.manage"))
        emp = _employee(organization, office, "H1-3")
        service = EmployeeAttachmentService()
        record = service.upload(hr_1, upload=_pdf(), purpose="document")
        with pytest.raises(ValidationFailed):
            service.attach_document(hr_2, emp.id, kind="OTHER", file_id=record.id)

    def test_own_fresh_upload_still_attaches(self, organization, region):
        """Законный путь: загрузил сам — прикрепил сам, в своей области."""
        user, actor, office_a, _ = _combo(
            organization, region,
            write=("employees.read", "employees.manage"),
            read=("employees.read",), tag="H1OK")
        emp_a = _employee(organization, office_a, "H1OK-A")
        client = _client(user)
        uploaded = client.post(f"{API}/employees/attachments/",
                               {"file": _pdf(), "purpose": "document"},
                               format="multipart")
        assert uploaded.status_code == 201, uploaded.content
        file_id = uploaded.json()["id"]
        attach = client.post(f"{API}/employees/{emp_a.id}/documents/",
                             {"kind": "IDENTITY", "file_id": file_id}, format="json")
        assert attach.status_code == 201, attach.content
        download = client.get(
            f"{API}/employees/{emp_a.id}/documents/{attach.json()['id']}/download/")
        assert download.status_code == 200


# =============================================================================
# M-1. Контекст обращения
# =============================================================================


class TestQuestionContextScope:
    """Блоки контекста — по области своего права для этого сотрудника."""

    def _ask(self, employee):
        from django_tests.test_questions_inbox import ask

        return ask(employee, "Когда мой отпуск?")

    def test_union_permission_does_not_reveal_foreign_absences(self, organization, region):
        # questions.read на всю организацию + absences/employees.read только в A.
        office_a = make_office(organization, region, "M1-A")
        office_b = make_office(organization, region, "M1-B")
        user, _ = create_actor(organization, permissions=("questions.read",),
                               role_code="M1_Q_ORG")
        _add_grant(organization, user,
                   permissions=("absences.read", "employees.read",
                                "attendance.read"),
                   office=office_a, role_code="M1_READ_A")
        emp_a = _employee(organization, office_a, "M1-EA")
        emp_b = _employee(organization, office_b, "M1-EB")
        client = _client(user)
        url = f"{API}/knowledge/escalations"

        foreign = client.get(f"{url}/{self._ask(emp_b).id}/context/")
        assert foreign.status_code == 200, foreign.content
        body = foreign.json()
        for block in ("requests", "balance", "corrections", "documents", "today"):
            assert body[block] is None, block
        assert body["links"] == {"employee_card": False, "attendance": False,
                                 "requests": False}

        own = client.get(f"{url}/{self._ask(emp_a).id}/context/").json()
        assert own["requests"] is not None
        assert own["documents"] is not None
        assert own["links"]["requests"] is True


# =============================================================================
# M-6. Запись — по текущему назначению, чтение — по истории
# =============================================================================


class TestWriteScopeFollowsCurrentAssignment:

    def _transferred(self, organization, region):
        """Сотрудник был в A до вчера, теперь в B."""
        office_a = make_office(organization, region, "M6-A")
        office_b = make_office(organization, region, "M6-B")
        yesterday = date.today() - timedelta(days=1)
        emp = _employee(organization, office_a, "M6-E",
                        valid_to=yesterday)
        EmployeeAssignment.objects.create(
            organization=organization, employee=emp, office=office_b,
            employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
            valid_from=date.today(),
        )
        return emp, office_a, office_b

    def test_previous_office_hr_cannot_edit_transferred(self, organization, region):
        emp, office_a, office_b = self._transferred(organization, region)
        hr_a = create_actor(organization, office=office_a,
                            permissions=("employees.read", "employees.manage"))[0]
        client = _client(hr_a)
        # Чтение истории осталось: карточка открывается.
        assert client.get(f"{API}/employees/{emp.id}/").status_code == 200
        # Правка — отказ.
        edit = client.patch(f"{API}/employees/{emp.id}/", {"phone": "+998901112233"},
                            format="json")
        assert edit.status_code == 403, edit.content
        assert Employee.objects.get(id=emp.id).phone != "+998901112233"

    def test_current_office_hr_still_edits(self, organization, region):
        emp, _, office_b = self._transferred(organization, region)
        hr_b = create_actor(organization, office=office_b,
                            permissions=("employees.read", "employees.manage"))[0]
        edit = _client(hr_b).patch(f"{API}/employees/{emp.id}/",
                                   {"phone": "+998901112233"}, format="json")
        assert edit.status_code == 200, edit.content

    def test_dismissed_employee_stays_with_last_office(self, organization, region):
        office_a = make_office(organization, region, "M6D-A")
        office_b = make_office(organization, region, "M6D-B")
        emp = _employee(organization, office_a, "M6D-E",
                        valid_to=date.today() - timedelta(days=200))
        EmployeeAssignment.objects.create(
            organization=organization, employee=emp, office=office_b,
            employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
            valid_from=date.today() - timedelta(days=199),
            valid_to=date.today() - timedelta(days=10),
        )
        Employee.objects.filter(id=emp.id).update(employment_status="TERMINATED")
        from humotech.core.rbac import AccessControl
        from humotech.employees.selectors import require_visible_employee
        from humotech.core.errors import PermissionDenied

        _, hr_b = create_actor(organization, office=office_b,
                               permissions=("employees.read", "employees.manage"))
        _, hr_a = create_actor(organization, office=office_a,
                               permissions=("employees.read", "employees.manage"))
        access = AccessControl()
        # Последний офис уволенного — B: его HR сохраняет документы.
        require_visible_employee(access, hr_b, emp.id, "employees.manage")
        with pytest.raises(PermissionDenied):
            require_visible_employee(access, hr_a, emp.id, "employees.manage")
        # Читать карточку уволенного может и HR прежнего офиса.
        require_visible_employee(access, hr_a, emp.id, "employees.read")
        require_visible_employee(access, hr_a, emp.id)


# =============================================================================
# M-5 / L-10. Файл медицинской справки
# =============================================================================

from django_tests.test_security_files import (  # noqa: E402  фикстуры больничного
    context, hr_url, sick, sick_leave,
)


class TestCertificateFile:
    """Файл справки открывают `absences.documents` или `absences.approve`
    в области этого права (решение владельца 11.10.2026)."""

    @pytest.mark.parametrize("perms", [
        ("employees.read", "absences.read"),                       # VIEWER
        ("employees.read", "absences.read", "reports.export"),     # ACCOUNTANT
        ("employees.read", "absences.read", "attendance.manual"),  # OFFICE_ADMIN
    ])
    def test_readers_without_decision_rights_get_403(self, organization, office,
                                                      sick, perms):
        request, document = sick
        user = create_actor(organization, permissions=perms, office=office)[0]
        refused = _client(user).get(hr_url(request, document))
        assert refused.status_code == 403
        assert b"%PDF" not in b"".join(getattr(refused, "streaming_content", [refused.content]))

    @pytest.mark.parametrize("perms", [
        ("absences.read", "absences.approve"),     # MANAGER
        ("absences.read", "absences.documents"),   # HR
    ])
    def test_decision_rights_in_scope_open_file(self, organization, office, sick, perms):
        request, document = sick
        user = create_actor(organization, permissions=perms, office=office)[0]
        assert _client(user).get(hr_url(request, document)).status_code == 200

    def test_approve_in_a_does_not_open_file_seen_via_viewer_in_b(
            self, organization, region, office, sick):
        """Комбо: решает в офисе A, просто видит офис B (где больной)."""
        request, document = sick
        office_a = make_office(organization, region, "M5-A")
        user, _ = create_actor(organization, office=office_a,
                               permissions=("absences.read", "absences.approve"))
        _add_grant(organization, user, permissions=("absences.read",),
                   office=office, role_code="M5_VIEW_B")
        assert _client(user).get(hr_url(request, document)).status_code == 404

    def test_dismissed_employee_file_stays_with_last_office_hr(
            self, organization, office, sick, employee):
        """L-10: увольнение закрывает назначения — HR его офиса справку не теряет."""
        request, document = sick
        EmployeeAssignment.objects.filter(employee=employee).update(
            valid_to=date.today() - timedelta(days=1))
        Employee.objects.filter(id=employee.id).update(employment_status="TERMINATED")
        hr = create_actor(organization, office=office,
                          permissions=("absences.read", "absences.documents"))[0]
        assert _client(hr).get(hr_url(request, document)).status_code == 200
