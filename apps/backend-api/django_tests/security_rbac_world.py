"""Общий мир для проверок зоны rbac (test_security_rbac_*.py).

Не тестовый модуль: здесь только построители данных и снимок состояния
базы. Все имена и данные вымышленные.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from django_tests.conftest import create_actor, make_office, make_qr_point, make_work_schedule
from humotech.core.permissions_catalog import ALL_PERMISSION_CODES, ROLE_PERMISSIONS
from humotech.departments.models import Department
from humotech.employees.models import Employee, EmployeeAssignment, EmployeeDocument
from humotech.organizations.models import Organization
from humotech.positions.models import Position
from humotech.regions.models import Region
from humotech.schedules.models import CalendarException, EmployeeScheduleAssignment

# Минимальный настоящий PNG 1x1: хранилище сверяет первые байты.
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
PDF_MIN = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def client_for(user, *, raise_errors: bool = False) -> APIClient:
    """Клиент, у которого 500 — это ответ, а не исключение в тесте."""
    client = APIClient(raise_request_exception=raise_errors)
    client.force_authenticate(user=user)
    return client


def new_org(code_prefix: str) -> Organization:
    return Organization.objects.create(
        code=f"{code_prefix}{uuid.uuid4().hex[:8]}", name=f"Компания {code_prefix}",
        default_timezone="Asia/Dushanbe", status="ACTIVE",
    )


def new_employee(org, office, number: str, *, department=None, position=None) -> Employee:
    emp = Employee.objects.create(
        organization=org, employee_number=number, first_name="Тест",
        last_name=f"Сотрудник{number}", hire_date=date(2024, 2, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=org, employee=emp, office=office, department=department,
        position=position, employment_type="FULL_TIME", work_mode="ONSITE",
        is_primary=True, valid_from=date(2024, 2, 1),
    )
    return emp


def _store(org, name, content, mime, prefix):
    from humotech.files.storage import store

    return store(
        SimpleUploadedFile(name, content, content_type=mime),
        organization_id=org.id, employee=None,
        allowed_types=("image/png", "application/pdf"),
        max_bytes=1024 * 1024, prefix=prefix,
    ).file


def attach_papers(org, employee) -> EmployeeDocument:
    """Фото и одна бумага сотруднику — чтобы было что скачивать."""
    photo = _store(org, "face.png", PNG_1X1, "image/png", "employees/photos")
    employee.photo = photo
    employee.save(update_fields=["photo"])
    paper = _store(org, "contract.pdf", PDF_MIN, "application/pdf", "employees/documents")
    return EmployeeDocument.objects.create(
        organization=org, employee=employee, kind="OTHER", title="Договор",
        status="UPLOADED", file=paper,
    )


def build_org_world(prefix: str = "B") -> dict:
    """Полная организация: справочники, сотрудник, файлы, доступы.

    Хозяин — HR со всеми разрешениями на всю организацию (`owner`):
    он же служит положительным контролем.
    """
    org = new_org(prefix)
    region = Region.objects.create(organization=org, code="R1", name="Регион", status="ACTIVE")
    office = make_office(org, region, "OFF1")
    department = Department.objects.create(
        organization=org, office=office, code="D1", name="Отдел", status="ACTIVE",
    )
    position = Position.objects.create(organization=org, code="P1", name="Должность", status="ACTIVE")
    schedule = make_work_schedule(org)
    employee = new_employee(org, office, f"{prefix}-0001", department=department, position=position)
    EmployeeScheduleAssignment.objects.create(
        organization=org, employee=employee, schedule=schedule, valid_from=date(2024, 2, 1),
    )
    document = attach_papers(org, employee)
    owner, owner_actor = create_actor(org, permissions=ALL_PERMISSION_CODES)
    from humotech.absences.models import AbsenceType
    from humotech.accounts.models import UserRoleScope

    grant = UserRoleScope.objects.get(user=owner)

    absence_type = AbsenceType.objects.create(
        organization=org, code="SICK_LEAVE", name="Больничный", is_paid=True,
        requires_approval=True,
    )
    calendar = CalendarException.objects.create(
        organization=org, office=office, date=date(2030, 1, 7), name="Праздник",
        exception_type="HOLIDAY", is_working_day=False,
    )
    qr_point = make_qr_point(org, office)
    return {
        "org": org, "region": region, "office": office, "department": department,
        "position": position, "schedule": schedule, "employee": employee,
        "document": document, "owner": owner, "owner_actor": owner_actor,
        "grant": grant, "role": grant.role, "absence_type": absence_type,
        "calendar": calendar, "qr_point": qr_point,
    }


def add_invitation(world: dict) -> None:
    from humotech.telegram.services import TelegramLinkService

    issued = TelegramLinkService().create_invitation(world["owner_actor"], world["employee"].id)
    world["invitation"] = issued.invitation


def add_absence_request(world: dict) -> None:
    from humotech.absences.models import AbsenceRequest

    start = datetime(2030, 3, 1, 4, 0, tzinfo=dt_timezone.utc)
    world["absence_request"] = AbsenceRequest.objects.create(
        organization=world["org"], employee=world["employee"],
        absence_type=world["absence_type"], request_kind="CREATE",
        requested_start_at=start, requested_end_at=start + timedelta(hours=12),
        status="SUBMITTED", submitted_at=start,
    )


def role_actor(org, role: str, *, region=None, office=None):
    """Учётка с разрешениями системной роли и заданной областью."""
    return create_actor(org, permissions=ROLE_PERMISSIONS[role], region=region, office=office)[0]


# --- снимок состояния ------------------------------------------------------------


def _models():
    for model in apps.get_models():
        meta = model._meta
        if meta.managed and not meta.proxy and not meta.swapped:
            yield model


def db_state(org_id) -> dict:
    """Число строк каждой таблицы плюс отпечаток строк организации.

    Счётчики — по ВСЕЙ базе: запись, сделанная атакующим на свою
    организацию, но ссылающаяся на чужого сотрудника, в срез по
    организации жертвы не попала бы.
    """
    state: dict = {}
    for model in _models():
        table = model._meta.db_table
        state[f"count:{table}"] = model.objects.count()
        names = {f.name for f in model._meta.concrete_fields}
        if "organization" in names:
            rows = list(model.objects.filter(organization_id=org_id).order_by("pk").values())
            state[f"hash:{table}"] = hashlib.sha256(repr(rows).encode()).hexdigest()
    return state


def diff_state(before: dict, after: dict) -> list[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
