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


# =============================================================================
# M-3. Правка набора прав уже выданной роли
# =============================================================================


class TestRoleEditCoversGrants:

    def _service(self):
        from humotech.accounts.rbac_service import RoleAdminService

        return RoleAdminService()

    def _setup(self, organization, region):
        office_a = make_office(organization, region, "M3-A")
        office_b = make_office(organization, region, "M3-B")
        user, actor = create_actor(organization, office=office_a,
                                   permissions=("roles.manage", "employees.read"),
                                   role_code="M3_ROLES_A")
        _add_grant(organization, user, permissions=("employees.manage",),
                   office=office_b, role_code="M3_MANAGE_B")
        bob = create_actor(organization, permissions=())[0]
        custom = Role.objects.create(organization=organization, code="M3_CUSTOM",
                                     name="Своя")
        perm, _ = Permission.objects.get_or_create(
            code="employees.read", defaults={"name": "r", "description": "r"})
        RolePermission.objects.create(role=custom, permission=perm)
        return actor, office_a, office_b, bob, custom

    def test_cannot_add_permission_to_org_wide_grant(self, organization, region):
        from humotech.core.errors import PermissionDenied

        actor, _, _, bob, custom = self._setup(organization, region)
        UserRoleScope.objects.create(organization=organization, user=bob, role=custom,
                                     valid_from=datetime.now(tz=dt_timezone.utc))
        with pytest.raises(PermissionDenied):
            self._service().update_role(
                actor, custom.id, permissions=["employees.read", "employees.manage"])
        assert set(RolePermission.objects.filter(role=custom).values_list(
            "permission__code", flat=True)) == {"employees.read"}

    def test_cannot_strip_role_granted_outside_scope(self, organization, region):
        from humotech.core.errors import PermissionDenied

        actor, _, office_b, bob, custom = self._setup(organization, region)
        UserRoleScope.objects.create(organization=organization, user=bob, role=custom,
                                     office=office_b,
                                     valid_from=datetime.now(tz=dt_timezone.utc))
        with pytest.raises(PermissionDenied):
            self._service().update_role(actor, custom.id, permissions=[])

    def test_edit_role_granted_inside_own_scope(self, organization, region):
        actor, office_a, _, bob, custom = self._setup(organization, region)
        UserRoleScope.objects.create(organization=organization, user=bob, role=custom,
                                     office=office_a,
                                     valid_from=datetime.now(tz=dt_timezone.utc))
        # Снять право в своей области можно.
        self._service().update_role(actor, custom.id, permissions=[])
        assert not RolePermission.objects.filter(role=custom).exists()
        # А добавить employees.manage в A — нет: оно у актора только в B.
        from humotech.core.errors import PermissionDenied

        with pytest.raises(PermissionDenied):
            self._service().update_role(actor, custom.id,
                                        permissions=["employees.manage"])

    def test_org_wide_admin_unchanged(self, organization, region):
        _, admin = create_actor(organization,
                                permissions=("roles.manage", "employees.read",
                                             "employees.manage"))
        bob = create_actor(organization, permissions=())[0]
        custom = Role.objects.create(organization=organization, code="M3_ORG",
                                     name="Своя")
        UserRoleScope.objects.create(organization=organization, user=bob, role=custom,
                                     valid_from=datetime.now(tz=dt_timezone.utc))
        self._service().update_role(admin, custom.id,
                                    permissions=["employees.read", "employees.manage"])
        assert RolePermission.objects.filter(role=custom).count() == 2


# =============================================================================
# M-4. Выгрузка — в области reports.export
# =============================================================================


class TestExportConfinedToExportGrant:
    """ACCOUNTANT на офис A (единственное место с reports.export) + VIEWER на
    всю организацию не выгружает данные всей организации."""

    READ = ("employees.read", "attendance.read", "absences.read", "analytics.read")

    def _setup(self, organization, region):
        office_a = make_office(organization, region, "M4-A")
        office_b = make_office(organization, region, "M4-B")
        user, actor = create_actor(organization, office=office_a,
                                   permissions=(*self.READ, "reports.export"),
                                   role_code="M4_ACC_A")
        _add_grant(organization, user, permissions=self.READ,
                   role_code="M4_VIEW_ORG")  # вся организация
        emp_a = _employee(organization, office_a, "M4-EA")
        emp_b = _employee(organization, office_b, "M4-EB")
        return user, actor, office_a, office_b, emp_a, emp_b

    def test_legacy_export_rows_only_from_export_grant(self, organization, region):
        user, _, _, office_b, emp_a, emp_b = self._setup(organization, region)
        client = _client(user)
        response = client.get(f"{API}/reports/employees/export?fmt=csv")
        assert response.status_code == 200
        text = b"".join(response.streaming_content).decode("utf-8-sig") \
            if hasattr(response, "streaming_content") else response.content.decode("utf-8-sig")
        assert "M4-EA" in text
        assert "M4-EB" not in text
        # Чужой офис в фильтре — отказ, а не данные.
        foreign = client.get(f"{API}/reports/employees/export?office_id={office_b.id}")
        assert foreign.status_code in (403, 404)

    def test_builder_preview_offices_only_from_export_grant(self, organization, region):
        user, _, office_a, office_b, *_ = self._setup(organization, region)
        client = _client(user)
        today = date.today().isoformat()
        base = {"kind": "employees", "date_from": today, "date_to": today}
        assert client.post(f"{API}/reports/preview",
                           {**base, "office_ids": [str(office_b.id)]},
                           format="json").status_code in (403, 404)
        own = client.post(f"{API}/reports/preview",
                          {**base, "office_ids": [str(office_a.id)]}, format="json")
        assert own.status_code == 200, own.content
        whole = client.post(f"{API}/reports/preview", base, format="json")
        assert whole.status_code == 200
        assert "M4-EB" not in whole.content.decode()

    def test_org_wide_accountant_still_exports_everything(self, organization, region):
        office_a = make_office(organization, region, "M4W-A")
        office_b = make_office(organization, region, "M4W-B")
        _employee(organization, office_a, "M4W-EA")
        _employee(organization, office_b, "M4W-EB")
        user = create_actor(organization,
                            permissions=(*self.READ, "reports.export"))[0]
        response = _client(user).get(f"{API}/reports/employees/export?fmt=csv")
        text = b"".join(response.streaming_content).decode("utf-8-sig") \
            if hasattr(response, "streaming_content") else response.content.decode("utf-8-sig")
        assert "M4W-EA" in text and "M4W-EB" in text

    def test_download_any_covered_by_its_own_grant(self, organization, region):
        from humotech.reports.models import ExportJob
        from humotech.reports.service import ExportJobService

        office_a = make_office(organization, region, "M4D-A")
        office_b = make_office(organization, region, "M4D-B")
        author = create_actor(organization, permissions=("reports.export",))[0]
        job = ExportJob.objects.create(
            organization=organization, requested_by_user=author, kind="employees",
            fmt="csv", status="SUCCEEDED", filters={"office_id": str(office_b.id)},
        )
        user, actor = create_actor(organization, office=office_a,
                                   permissions=("reports.export", "reports.download_any"),
                                   role_code="M4D_ANY_A")
        _add_grant(organization, user, permissions=("employees.read",),
                   office=office_b, role_code="M4D_VIEW_B")
        assert ExportJobService()._covers_report(actor, job) is False
        _, wide = create_actor(organization,
                               permissions=("reports.export", "reports.download_any"))
        assert ExportJobService()._covers_report(wide, job) is True


# =============================================================================
# M-2. Общие для организации объекты
# =============================================================================

from humotech.core.errors import PermissionDenied  # noqa: E402


class TestSharedSchedules:
    """Решение владельца 11.10: региональный HR создаёт и меняет графики только
    своего региона; графики организации — только с правом на всю организацию."""

    def _schedule(self, actor, **extra):
        from humotech.schedules.services import WorkScheduleService

        return WorkScheduleService().create(actor, name="Пятидневка",
                                            timezone="Asia/Tashkent",
                                            weekly_minutes=2400, **extra)

    def test_regional_hr_creates_and_edits_only_own_region_schedules(
            self, organization, region, other_region):
        from humotech.schedules.services import WorkScheduleService

        office_1 = make_office(organization, region, "M2S-1")
        _, regional = create_actor(organization, region=region,
                                   permissions=("schedules.read", "schedules.manage"))
        _, wide = create_actor(organization,
                               permissions=("schedules.read", "schedules.manage"))
        service = WorkScheduleService()

        own = self._schedule(regional)                     # регион подставлен сам
        assert own.region_id == region.id
        service.update(regional, own.id, name="Своя")       # свой регион — можно

        org_schedule = self._schedule(wide)                 # график организации
        assert org_schedule.region_id is None
        with pytest.raises(PermissionDenied):
            service.update(regional, org_schedule.id, name="Чужая")
        with pytest.raises(PermissionDenied):
            service.deactivate(regional, org_schedule.id)
        with pytest.raises(PermissionDenied):               # чужой регион — нельзя
            self._schedule(regional, region_id=other_region.id)

        # Права видны интерфейсу заранее.
        rows = {row.id: row.can_manage for row in service.list(regional).items}
        assert rows[own.id] is True and rows[org_schedule.id] is False
        # HR организации правит любые.
        service.update(wide, own.id, name="Общая")
        service.update(wide, org_schedule.id, name="Общая 2")
        _employee(organization, office_1, "M2S-E1")

    def test_region_schedule_assigned_only_inside_region(self, organization, region,
                                                         other_region):
        from humotech.core.errors import ValidationFailed as VF
        from humotech.schedules.services import WorkScheduleService

        office_2 = make_office(organization, other_region, "M2S-2")
        stranger = _employee(organization, office_2, "M2S-E2")
        _, wide = create_actor(organization,
                               permissions=("schedules.read", "schedules.manage",
                                            "employees.read"))
        regional_schedule = self._schedule(wide, region_id=region.id)
        with pytest.raises(VF):
            WorkScheduleService().assign_to_employee(
                wide, employee_id=stranger.id, schedule_id=regional_schedule.id,
                valid_from=date.today())

    def test_view_grant_does_not_count_as_whole_org(self, organization, region):
        """Комбо: schedules.manage на регион + чтение всей организации."""
        from humotech.schedules.services import WorkScheduleService

        user, actor = create_actor(organization, region=region,
                                   permissions=("schedules.read", "schedules.manage"),
                                   role_code="M2S_MANAGE_R1")
        _add_grant(organization, user, permissions=("schedules.read",),
                   role_code="M2S_READ_ORG")
        _, wide = create_actor(organization,
                               permissions=("schedules.read", "schedules.manage"))
        org_schedule = self._schedule(wide)
        with pytest.raises(PermissionDenied):
            WorkScheduleService().update(actor, org_schedule.id, name="x")

    def test_other_region_schedule_hidden_from_regional_reader(self, organization,
                                                               region, other_region):
        from humotech.core.errors import NotFound
        from humotech.schedules.services import WorkScheduleService

        _, wide = create_actor(organization,
                               permissions=("schedules.read", "schedules.manage"))
        foreign = self._schedule(wide, region_id=other_region.id)
        _, reader = create_actor(organization, region=region,
                                 permissions=("schedules.read",))
        assert foreign.id not in {r.id for r in WorkScheduleService().list(reader).items}
        with pytest.raises(NotFound):
            WorkScheduleService().get(reader, foreign.id)


class TestOrganizationWideObjects:

    def _office_actor(self, organization, region, perms, tag):
        office = make_office(organization, region, tag)
        user, actor = create_actor(organization, office=office, permissions=perms,
                                   role_code=f"{tag}_W")
        # Плюс чтение всей организации — оно не должно засчитываться.
        _add_grant(organization, user, permissions=("employees.read",),
                   role_code=f"{tag}_R")
        return office, actor

    def test_settings_update_requires_whole_org(self, organization, region):
        from humotech.organizations.settings_service import (
            ORGANIZATION_DEFAULTS_KEY, OrganizationSettingsService,
        )

        _, scoped = self._office_actor(organization, region, ("settings.manage",), "M2ST")
        with pytest.raises(PermissionDenied):
            OrganizationSettingsService().update(scoped, ORGANIZATION_DEFAULTS_KEY,
                                                 {"name": "Взлом"})

    def test_absence_types_require_whole_org(self, organization, region):
        from humotech.absences.types_service import AbsenceTypeService

        _, scoped = self._office_actor(organization, region,
                                       ("absences.read", "absences.manage_types"), "M2AT")
        with pytest.raises(PermissionDenied):
            AbsenceTypeService().create(scoped, name="Отгул")
        _, wide = create_actor(organization,
                               permissions=("absences.read", "absences.manage_types"))
        assert AbsenceTypeService().create(wide, name="Отгул").name == "Отгул"

    def test_positions_require_whole_org(self, organization, region):
        from humotech.departments.services import PositionService

        _, scoped = self._office_actor(organization, region, ("positions.manage",), "M2P")
        with pytest.raises(PermissionDenied):
            PositionService().create(scoped, name="Кассир")
        _, wide = create_actor(organization, permissions=("positions.manage",))
        assert PositionService().create(wide, name="Кассир").name == "Кассир"

    def test_shared_department_requires_whole_org(self, organization, region):
        from humotech.departments.services import DepartmentService

        office, scoped = self._office_actor(organization, region,
                                            ("departments.manage",), "M2D")
        _, wide = create_actor(organization, permissions=("departments.manage",))
        shared = DepartmentService().create(wide, name="Продажи")
        with pytest.raises(PermissionDenied):
            DepartmentService().update(scoped, shared.id, name="Взлом")
        with pytest.raises(PermissionDenied):
            DepartmentService().create(scoped, name="Общий")
        # Отдел своего офиса — можно, как прежде.
        own = DepartmentService().create(scoped, name="Склад", office_id=office.id)
        DepartmentService().update(scoped, own.id, name="Склад 2")

    def test_onboarding_program_and_policies_require_whole_org(self, organization,
                                                                region):
        from humotech.onboarding.services import OnboardingContentService, PolicyService

        _, scoped = self._office_actor(
            organization, region,
            ("onboarding.read", "onboarding.manage", "policies.publish"), "M2O")
        with pytest.raises(PermissionDenied):
            OnboardingContentService().create_section(scoped, {"title": "Взлом",
                                                               "body": "x"})
        with pytest.raises(PermissionDenied):
            PolicyService().create_document(scoped, {"code": "HACK", "title": "x"})


# =============================================================================
# M-7. Django Admin — только своя организация
# =============================================================================


class TestAdminOrganizationScope:

    def _admin_client(self, organization):
        from django.test import Client

        user = create_actor(organization, role_code="SUPER_ADMIN")[0]
        assert user.is_staff
        client = Client()
        client.force_login(user)
        return client, user

    def test_super_admin_sees_only_own_organization(self, organization,
                                                    other_organization):
        client, _ = self._admin_client(organization)
        foreign = create_actor(other_organization, permissions=())[0]
        mine = create_actor(organization, permissions=())[0]

        listing = client.get("/admin/accounts/user/")
        assert listing.status_code == 200
        html = listing.content.decode()
        assert mine.email in html
        assert foreign.email not in html

        # Карточка чужого пользователя — не открывается.
        card = client.get(f"/admin/accounts/user/{foreign.id}/change/")
        assert card.status_code in (302, 404)
        # Чужая организация в списке организаций не видна.
        orgs = client.get("/admin/organizations/organization/").content.decode()
        assert other_organization.code not in orgs
        assert organization.code in orgs

    def test_cannot_grant_role_in_foreign_organization(self, organization,
                                                      other_organization):
        client, _ = self._admin_client(organization)
        foreign_role = Role.objects.create(organization=other_organization,
                                           code="FOREIGN_R", name="Чужая")
        page = client.get("/admin/accounts/userrolescope/add/")
        assert page.status_code == 200
        assert str(foreign_role.id) not in page.content.decode()

    def test_system_role_is_read_only(self, organization):
        client, _ = self._admin_client(organization)
        system = Role.objects.create(organization=None, code="SYS_X", name="Системная")
        page = client.get(f"/admin/rbac/role/{system.id}/change/")
        assert page.status_code == 200
        assert 'name="_save"' not in page.content.decode()


# =============================================================================
# M-8. Блокировка учётки не выбивает владельца со знакомого адреса
# =============================================================================


class TestAccountLockDoesNotLockOwnerOut:
    PASSWORD = "Вымышленный-Пароль-2026"

    def _login(self, organization, email, password, ip):
        return APIClient().post(
            f"{API}/auth/login",
            {"organization_code": organization.code, "email": email,
             "password": password},
            format="json", REMOTE_ADDR=ip)

    def test_owner_on_known_address_survives_distributed_lock(self, organization,
                                                             make_user):
        owner = make_user(organization, permissions=("employees.read",),
                          raw_password=self.PASSWORD)
        office_ip = "198.51.100.10"
        assert self._login(organization, owner.email, self.PASSWORD,
                           office_ip).status_code == 200
        # Злоумышленник с разных адресов выбивает общий счётчик учётки.
        for n in range(21):
            self._login(organization, owner.email, "не тот", f"192.0.2.{n + 1}")
        # С нового адреса — заблокировано (перебор так и остаётся закрыт)…
        assert self._login(organization, owner.email, self.PASSWORD,
                           "203.0.113.200").status_code == 429
        # …а владелец со знакомого адреса входит.
        assert self._login(organization, owner.email, self.PASSWORD,
                           office_ip).status_code == 200

    def test_known_address_still_limited_per_pair(self, organization, make_user):
        """Знакомый адрес — не лазейка для перебора: пара «логин + адрес»."""
        owner = make_user(organization, permissions=("employees.read",),
                          raw_password=self.PASSWORD)
        ip = "198.51.100.11"
        assert self._login(organization, owner.email, self.PASSWORD, ip).status_code == 200
        codes = [self._login(organization, owner.email, "не тот", ip).status_code
                 for _ in range(6)]
        assert codes[-1] == 429


# =============================================================================
# M-9. Пересланный сменный QR — на проверку HR, проход не блокируется
# =============================================================================


class TestSharedRotatingCodeGoesToReview:

    def test_second_employee_from_other_address_is_review_but_counted(
            self, organization, office, employee, qr_point, now, fresh_qr):
        from humotech.attendance.services import register_scan

        first = register_scan(employee_id=employee.id, qr_point=qr_point, now=now,
                              qr_nonce_hash="shared-1", ip_address="10.0.0.1",
                              **fresh_qr)
        assert first.event.verification_status == "ACCEPTED"

        colleague = _employee(organization, office, "M9-COL")
        second = register_scan(employee_id=colleague.id, qr_point=qr_point, now=now,
                               qr_nonce_hash="shared-1", ip_address="10.0.0.2",
                               **fresh_qr)
        assert second.accepted, "проход не блокируется"
        assert second.session is not None
        assert second.event.verification_status == "REVIEW"
        assert "SHARED_CODE" in second.event.event_metadata["anomalies"]

    def test_same_address_colleague_is_plain_accepted(
            self, organization, office, employee, qr_point, now, fresh_qr):
        from humotech.attendance.services import register_scan

        register_scan(employee_id=employee.id, qr_point=qr_point, now=now,
                      qr_nonce_hash="shared-2", ip_address="10.0.0.1", **fresh_qr)
        colleague = _employee(organization, office, "M9-COL2")
        second = register_scan(employee_id=colleague.id, qr_point=qr_point, now=now,
                               qr_nonce_hash="shared-2", ip_address="10.0.0.1",
                               **fresh_qr)
        assert second.event.verification_status == "ACCEPTED"

    def test_review_mark_counts_as_checked_in(self, organization, office, employee,
                                              qr_point, now, fresh_qr):
        """Напоминание «вы не отметились» не уходит тому, чья отметка на проверке."""
        from humotech.attendance import reminders
        from humotech.attendance.services import register_scan

        register_scan(employee_id=employee.id, qr_point=qr_point, now=now,
                      qr_nonce_hash="shared-3", ip_address="10.0.0.1", **fresh_qr)
        colleague = _employee(organization, office, "M9-COL3")
        second = register_scan(employee_id=colleague.id, qr_point=qr_point, now=now,
                               qr_nonce_hash="shared-3", ip_address="10.0.0.2",
                               **fresh_qr)
        assert second.event.verification_status == "REVIEW"
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Asia/Dushanbe")
        assert reminders._already_marked(colleague.id, now.astimezone(zone).date(), zone)



# =============================================================================
# M-10. Ответы API с ПДн не кэшируются
# =============================================================================


class TestApiResponsesAreNotCached:

    def test_legacy_export_is_no_store(self, organization, office):
        _employee(organization, office, "M10-E")
        user = create_actor(organization,
                            permissions=("employees.read", "reports.export"))[0]
        response = _client(user).get(f"{API}/reports/employees/export?fmt=csv")
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_hr_application_pdf_is_no_store(self, organization, office, sick):
        request, _ = sick
        user = create_actor(organization, office=office,
                            permissions=("absences.read", "absences.approve"))[0]
        response = _client(user).get(f"{API}/absence-requests/{request.id}/application")
        assert response.status_code == 200, response.content[:200]
        assert response["Cache-Control"] == "private, no-store"

    def test_json_api_is_no_store_but_views_keep_their_own(self, organization, office,
                                                          sick):
        request, document = sick
        user = create_actor(organization, office=office,
                            permissions=("employees.read", "absences.read",
                                         "absences.documents"))[0]
        client = _client(user)
        card = client.get(f"{API}/employees/")
        assert card["Cache-Control"] == "private, no-store"
        # Свой заголовок выдачи файла не перетирается.
        assert client.get(hr_url(request, document))["Cache-Control"] == "private, no-store"


# =============================================================================
# L-1, L-2, L-12. Telegram-привязка и вход в админку
# =============================================================================


class TestTelegramLinkHardening:

    def test_link_from_group_chat_is_refused(self):
        from humotech.telegram.serializers import BotConsumeSerializer, BotRecognizeSerializer

        group = {"token": "t" * 43, "telegram_user_id": 5_101,
                 "telegram_chat_id": -100_123_456}
        assert not BotConsumeSerializer(data=group).is_valid()
        assert not BotRecognizeSerializer(data={k: v for k, v in group.items()
                                                if k != "token"}).is_valid()
        private = {**group, "telegram_chat_id": 5_101}
        assert BotConsumeSerializer(data=private).is_valid(), \
            BotConsumeSerializer(data=private).errors


def test_admin_password_login_is_impossible(organization):
    """L-12 не подтвердился: форма админки не передаёт код организации, а
    вход без него невозможен — перебирать пароль в админке нечем."""
    from django.test import Client

    user = create_actor(organization, role_code="SUPER_ADMIN",
                        raw_password="Админ-Вымышленный-2026")[0]
    response = Client().post("/admin/login/?next=/admin/",
                             {"username": user.email,
                              "password": "Админ-Вымышленный-2026"})
    assert response.status_code == 200  # форма вернулась с ошибкой, не 302
    assert "_auth_user_id" not in Client().session


# =============================================================================
# L-3, L-4, L-5, L-6, L-8, L-11
# =============================================================================


class TestLowFindings:

    def test_l3_link_throttle_is_per_telegram_user(self):
        from rest_framework.test import APIRequestFactory

        from humotech.telegram.views import BotLinkView, PerTelegramUserThrottle

        factory = APIRequestFactory()
        throttle = PerTelegramUserThrottle()
        view = BotLinkView()

        def key(uid):
            from rest_framework.request import Request
            from rest_framework.parsers import JSONParser

            raw = factory.post("/x", {"telegram_user_id": uid}, format="json")
            request = Request(raw, parsers=[JSONParser()])
            throttle.scope = "telegram_bot_link"
            return throttle.get_cache_key(request, view)

        assert key(1001) != key(1002)
        assert key(1001) == key(1001)

    def test_l4_cluster_actions_use_answer_scope(self, organization, region):
        from humotech.ai_assistant.models import UnansweredQuestion
        from humotech.core.errors import NotFound
        from humotech.questions.service import QuestionService

        office_a = make_office(organization, region, "L4-A")
        office_b = make_office(organization, region, "L4-B")
        user, actor = create_actor(organization, office=office_a,
                                   permissions=("questions.read", "questions.answer"),
                                   role_code="L4_ANSWER_A")
        _add_grant(organization, user, permissions=("questions.read",),
                   office=office_b, role_code="L4_READ_B")
        cluster_b = UnansweredQuestion.objects.create(
            organization=organization, normalized_hash="4" * 64,
            question_text="Вопрос из B", language="ru", status="NEW", office=office_b)
        # Читать можно (B виден на чтение)…
        QuestionService().get_unanswered(actor, cluster_b.id)
        # …а распоряжаться — нет.
        with pytest.raises(NotFound):
            QuestionService().close_unanswered(actor, cluster_b.id, as_answered=False)

    def test_l5_survey_remind_requires_whole_org(self, organization, office):
        from humotech.surveys.services import SurveyCampaignService

        _, scoped = create_actor(organization, office=office,
                                 permissions=("surveys.read", "surveys.manage"))
        import uuid as _uuid
        with pytest.raises(PermissionDenied):
            SurveyCampaignService().remind(scoped, _uuid.uuid4())

    def test_l6_office_in_region_needs_whole_region(self, organization, region,
                                                    other_region):
        from django_tests.test_hr_org_structure import PLACE
        from humotech.offices.services import OfficeService

        office_a = make_office(organization, region, "L6-A")
        _, scoped = create_actor(organization, office=office_a,
                                 permissions=("offices.read", "offices.manage"))
        with pytest.raises(PermissionDenied):
            OfficeService().create(scoped, region_id=region.id, name="Новый", **PLACE)
        with pytest.raises(PermissionDenied):
            OfficeService().update(scoped, office_a.id, region_id=other_region.id)
        _, regional = create_actor(organization, region=region,
                                   permissions=("offices.read", "offices.manage"))
        created = OfficeService().create(regional, region_id=region.id, name="Новый",
                                         **PLACE)
        assert created.region_id == region.id

    def test_l8_remind_many_hides_foreign_state(self, organization, region):
        from humotech.onboarding.services import OnboardingService

        office_a = make_office(organization, region, "L8-A")
        office_b = make_office(organization, region, "L8-B")
        emp_b = _employee(organization, office_b, "L8-EB")
        _, actor = create_actor(organization, office=office_a,
                                permissions=("onboarding.read", "onboarding.manage"))
        result = OnboardingService().remind_many(actor, [emp_b.id])
        assert result == [{"employee_id": str(emp_b.id), "outcome": "not_enrolled"}]

    def test_l8_foreign_export_jobs_need_org_wide_audit(self, organization, office):
        from humotech.core.errors import NotFound
        from humotech.reports.models import ExportJob
        from humotech.reports.service import ExportJobService

        author = create_actor(organization, permissions=("reports.export",))[0]
        job = ExportJob.objects.create(organization=organization,
                                       requested_by_user=author, kind="employees",
                                       fmt="csv", status="QUEUED", filters={})
        _, scoped = create_actor(organization, office=office,
                                 permissions=("reports.export", "audit.read"))
        with pytest.raises(NotFound):
            ExportJobService().get(scoped, job.id)
        _, wide = create_actor(organization, permissions=("reports.export", "audit.read"))
        assert ExportJobService().get(wide, job.id).id == job.id

    def test_l8_user_list_limited_to_users_manage_territory(self, organization, region,
                                                           other_region):
        from humotech.accounts.rbac_service import UserAdminService

        office_a = make_office(organization, region, "L8U-A")
        office_b = make_office(organization, other_region, "L8U-B")
        _, actor = create_actor(organization, office=office_a,
                                permissions=("users.manage",))
        in_a = create_actor(organization, office=office_a, permissions=("employees.read",))[0]
        in_b = create_actor(organization, office=office_b, permissions=("employees.read",))[0]
        org_wide = create_actor(organization, permissions=("employees.read",))[0]
        seen = {user.id for user in UserAdminService().list(actor, limit=100).items}
        assert in_a.id in seen and actor.user_id in seen
        assert in_b.id not in seen and org_wide.id not in seen

    def test_l11_session_has_absolute_age(self, organization, make_user, settings):
        import time as _time

        from humotech.accounts.session_age import AUTH_AT_KEY

        password = "Вымышленный-Пароль-2026"
        user = make_user(organization, permissions=("employees.read",),
                         raw_password=password)
        client = APIClient()
        assert client.post(f"{API}/auth/login",
                           {"organization_code": organization.code,
                            "email": user.email, "password": password},
                           format="json", REMOTE_ADDR="198.51.100.40").status_code == 200
        assert client.get(f"{API}/auth/me").status_code == 200
        session = client.session
        session[AUTH_AT_KEY] = int(_time.time()) - settings.SESSION_ABSOLUTE_AGE - 5
        session.save()
        assert client.get(f"{API}/auth/me").status_code in (401, 403)


# =============================================================================
# N-1 (инвентаризация §11.7). Журнал аудита неизменяем и на уровне БД
# =============================================================================


class TestAuditLogAppendOnlyInDatabase:

    def _row(self, organization):
        from humotech.audit.models import AuditLog

        return AuditLog.objects.create(organization=organization, action="x.test",
                                       entity_type="tests", entity_id=organization.id,
                                       new_values={"a": 1})

    def test_update_and_delete_bypassing_model_are_refused(self, organization):
        from django.db import DatabaseError, transaction

        from humotech.audit.models import AuditLog

        row = self._row(organization)
        with pytest.raises(DatabaseError), transaction.atomic():
            AuditLog.objects.filter(id=row.id).update(action="x.forged")
        with pytest.raises(DatabaseError), transaction.atomic():
            AuditLog.objects.filter(id=row.id).delete()
        assert AuditLog.objects.get(id=row.id).action == "x.test"

    def test_seeder_purge_and_user_deletion_still_work(self, organization):
        from humotech.audit.models import AuditLog
        from humotech.audit.purge import allow_audit_purge

        user = create_actor(organization, permissions=())[0]
        kept = AuditLog.objects.create(organization=organization, action="x.by",
                                       entity_type="tests", entity_id=organization.id,
                                       actor_user=user)
        UserRoleScope.objects.filter(user=user).delete()
        user.delete()  # ON DELETE SET NULL — разрешённое изменение
        kept.refresh_from_db()
        assert kept.actor_user_id is None and kept.action == "x.by"

        row = self._row(organization)
        with allow_audit_purge():
            AuditLog.objects.filter(id=row.id).delete()
        assert not AuditLog.objects.filter(id=row.id).exists()


# =============================================================================
# L-7. База знаний — по организации, области и правам
# =============================================================================


class TestKnowledgeReadScope:

    def test_reader_sees_org_own_region_and_office_only(self, organization, region,
                                                        other_region, other_organization):
        from django_tests.test_knowledge_api import make_faq, make_source

        office_a = make_office(organization, region, "L7-A")
        office_b = make_office(organization, region, "L7-B")
        office_c = make_office(organization, other_region, "L7-C")
        author = create_actor(organization, permissions=())[0]
        org_wide = make_source(organization, author, title="Общий")
        own_region = make_source(organization, author, title="Регион", region=region)
        own_office = make_source(organization, author, title="Офис A", office=office_a)
        other_office = make_source(organization, author, title="Офис B", office=office_b)
        other_region_doc = make_source(organization, author, title="Чужой регион",
                                       region=other_region)
        foreign_author = create_actor(other_organization, permissions=())[0]
        foreign = make_source(other_organization, foreign_author, title="Соседи")
        faq_b = make_faq(organization, author, office=office_b)

        reader = create_actor(organization, office=office_a,
                              permissions=("knowledge.read",))[0]
        client = _client(reader)
        listed = {row["id"] for row in client.get(
            f"{API}/knowledge/sources/", {"limit": 100}).json()["items"]}
        assert {str(org_wide.id), str(own_region.id), str(own_office.id)} <= listed
        assert str(other_office.id) not in listed
        assert str(other_region_doc.id) not in listed
        assert str(foreign.id) not in listed
        # По угаданному id — тоже нет.
        assert client.get(f"{API}/knowledge/sources/{other_office.id}/").status_code == 404
        assert client.get(f"{API}/knowledge/sources/{foreign.id}/").status_code == 404
        assert client.get(f"{API}/knowledge/faq/{faq_b.id}/").status_code == 404
        # Читатель всей организации видит всё своё.
        wide = create_actor(organization, permissions=("knowledge.read",))[0]
        everything = {row["id"] for row in _client(wide).get(
            f"{API}/knowledge/sources/", {"limit": 100}).json()["items"]}
        assert str(other_office.id) in everything and str(foreign.id) not in everything
        office_c  # офис другого региона заведён для полноты картины


# =============================================================================
# M-11. Антивирусная проверка загрузок (clamd INSTREAM)
# =============================================================================


class _FakeClamd:
    """Мини-clamd: принимает INSTREAM и отвечает заданной строкой."""

    def __init__(self, reply: bytes):
        import socket
        import threading

        self.reply = reply
        self.received = b""
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        import struct

        conn, _ = self.sock.accept()
        with conn:
            data = b""
            while True:
                part = conn.recv(65536)
                if not part:
                    break
                data += part
                body = data[len(b"zINSTREAM\0"):]
                # конец потока — кусок нулевой длины
                pos, done = 0, False
                while pos + 4 <= len(body):
                    (size,) = struct.unpack("!L", body[pos:pos + 4])
                    if size == 0:
                        done = True
                        break
                    pos += 4 + size
                if done:
                    break
            self.received = data
            conn.sendall(self.reply)


class TestAntivirusScan:

    def _settings(self, settings, port):
        settings.FILES = {**settings.FILES, "SCANNER_ENABLED": True,
                          "SCANNER_HOST": "127.0.0.1", "SCANNER_PORT": port,
                          "SCANNER_TIMEOUT_SECONDS": 5}

    def _upload(self, organization, office, hr):
        return EmployeeAttachmentService().upload(hr, upload=_pdf(), purpose="document")

    def test_clean_file_stored_as_clean(self, settings, organization, office):
        fake = _FakeClamd(b"stream: OK\0")
        self._settings(settings, fake.port)
        _, hr = create_actor(organization, permissions=("employees.read", "employees.manage"))
        record = self._upload(organization, office, hr)
        assert record.scan_status == "CLEAN"
        assert fake.received.startswith(b"zINSTREAM\0") and PDF in fake.received

    def test_infected_file_is_refused_and_not_stored(self, settings, organization, office):
        from humotech.files.models import File

        fake = _FakeClamd(b"stream: Eicar-Test-Signature FOUND\0")
        self._settings(settings, fake.port)
        _, hr = create_actor(organization, permissions=("employees.read", "employees.manage"))
        before = File.objects.count()
        with pytest.raises(ValidationFailed) as error:
            self._upload(organization, office, hr)
        assert error.value.details["reason"] == "infected"
        assert File.objects.count() == before

    def test_scanner_down_refuses_with_503(self, settings, organization, office):
        import socket

        from humotech.core.errors import ScannerUnavailable

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # порт свободен — clamd «не запущен»
        self._settings(settings, port)
        _, hr = create_actor(organization, permissions=("employees.read", "employees.manage"))
        with pytest.raises(ScannerUnavailable):
            self._upload(organization, office, hr)
        assert ScannerUnavailable.http_status == 503

    def test_garbled_reply_is_not_a_verdict(self, settings, organization, office):
        from humotech.core.errors import ScannerUnavailable

        fake = _FakeClamd(b"INSTREAM size limit exceeded. ERROR\0")
        self._settings(settings, fake.port)
        _, hr = create_actor(organization, permissions=("employees.read", "employees.manage"))
        with pytest.raises(ScannerUnavailable):
            self._upload(organization, office, hr)

    def test_scanner_off_keeps_unscanned(self, organization, office):
        _, hr = create_actor(organization, permissions=("employees.read", "employees.manage"))
        assert self._upload(organization, office, hr).scan_status == "UNSCANNED"


# =============================================================================
# Независимая проверка 11.10 (F-2, F-4, F-6, F-8, F-9, F-1)
# =============================================================================


class TestReviewFollowUps:

    def test_f2_export_rows_are_intersection_with_data_permission(self, organization,
                                                                  region):
        """reports.export на всю организацию + employees.read только в A:
        выгрузка сотрудников — только из A."""
        office_a = make_office(organization, region, "F2-A")
        office_b = make_office(organization, region, "F2-B")
        _employee(organization, office_a, "F2-EA")
        _employee(organization, office_b, "F2-EB")
        user, _ = create_actor(organization, permissions=("reports.export",),
                               role_code="F2_EXPORT_ORG")
        _add_grant(organization, user, permissions=("employees.read",),
                   office=office_a, role_code="F2_READ_A")
        response = _client(user).get(f"{API}/reports/employees/export?fmt=csv")
        assert response.status_code == 200
        text = b"".join(response.streaming_content).decode("utf-8-sig") \
            if hasattr(response, "streaming_content") else response.content.decode("utf-8-sig")
        assert "F2-EA" in text and "F2-EB" not in text

    def test_f1_direct_null_of_actor_is_refused(self, organization):
        from django.db import DatabaseError, transaction

        from humotech.audit.models import AuditLog

        user = create_actor(organization, permissions=())[0]
        row = AuditLog.objects.create(organization=organization, action="x.f1",
                                      entity_type="tests", entity_id=organization.id,
                                      actor_user=user)
        with pytest.raises(DatabaseError), transaction.atomic():
            AuditLog.objects.filter(id=row.id).update(actor_user=None)
        row.refresh_from_db()
        assert row.actor_user_id == user.id

    def test_f4_admin_filters_show_only_own_organization(self, organization,
                                                         other_organization):
        from django.test import Client

        admin_user = create_actor(organization, role_code="SUPER_ADMIN")[0]
        Role.objects.create(organization=other_organization, code="F4_FOREIGN",
                            name="Чужая роль F4")
        client = Client()
        client.force_login(admin_user)
        html = client.get("/admin/accounts/user/").content.decode()
        assert other_organization.name not in html
        html = client.get("/admin/accounts/userrolescope/").content.decode()
        assert "Чужая роль F4" not in html

    def test_f6_review_duplicates_blocked_by_database(self, organization, office,
                                                      employee, qr_point, now):
        from django.db import IntegrityError, transaction

        from humotech.attendance.models import AttendanceEvent

        common = dict(organization=organization, employee=employee, office=office,
                      qr_point=qr_point, event_type="ENTRY", occurred_at=now,
                      received_at=now, source="QR", qr_nonce_hash="f6-nonce")
        AttendanceEvent.objects.create(verification_status="REVIEW", **common)
        with pytest.raises(IntegrityError), transaction.atomic():
            AttendanceEvent.objects.create(verification_status="REVIEW", **common)
        with pytest.raises(IntegrityError), transaction.atomic():
            AttendanceEvent.objects.create(verification_status="ACCEPTED", **common)
        # Отклонённая попытка nonce не занимает.
        AttendanceEvent.objects.create(verification_status="REJECTED", **common)

    def test_f8_region_change_needs_whole_region(self, organization, region):
        from humotech.regions.services import RegionService

        office = make_office(organization, region, "F8-A")
        _, scoped = create_actor(organization, office=office,
                                 permissions=("regions.read", "regions.manage"))
        with pytest.raises(PermissionDenied):
            RegionService().update(scoped, region.id, name="Взлом")
        _, regional = create_actor(organization, region=region,
                                   permissions=("regions.read", "regions.manage"))
        RegionService().update(regional, region.id, name="Свой регион")

    def test_f9_certificate_name_only_for_who_can_open(self, organization, office, sick):
        request, _ = sick
        viewer = create_actor(organization, office=office,
                              permissions=("absences.read", "employees.read"))[0]
        hr = create_actor(organization, office=office,
                          permissions=("absences.read", "absences.documents"))[0]
        url = f"{API}/absence-requests/{request.id}"
        seen = _client(viewer).get(url).json()["documents"][0]
        assert seen["file"]["name"] is None and seen["verification_comment"] is None
        assert seen["verification_status"]  # факт и состояние — видны
        assert _client(hr).get(url).json()["documents"][0]["file"]["name"] == "spravka.pdf"
