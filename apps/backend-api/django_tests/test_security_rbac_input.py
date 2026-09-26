"""Входные данные справочников и кадров (зона rbac).

Кривые UUID, даты, числа, NUL, поля длиннее колонки, координаты,
файлы чужого назначения, привязка Telegram при приёме и права
заблокированной учётки. Везде ожидается понятный 4xx, а не 500.
Клиент не пробрасывает исключения: 500 — это ответ, который видно.
"""

from __future__ import annotations

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from django_tests.conftest import HR_FULL_PERMISSIONS
from django_tests.security_rbac_world import PDF_MIN, PNG_1X1, attach_papers, client_for
from django_tests.test_hr_employee_onboarding import department, form, position  # noqa: F401
from humotech.core.rbac import AccessControl
from humotech.employees.attachments import EmployeeAttachmentService
from humotech.employees.onboarding import EmployeeOnboardingService
from humotech.files.storage import store
from humotech.telegram.models import TelegramAccount

pytestmark = pytest.mark.django_db

API = "/api/v1"
BAD = "not-a-uuid"


@pytest.fixture()
def hr(make_user, organization):
    return client_for(make_user(organization, permissions=HR_FULL_PERMISSIONS))


@pytest.fixture()
def private_files(settings, tmp_path):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path), "SCANNER_ENABLED": False}


# --- кривые идентификаторы -------------------------------------------------------


@pytest.mark.parametrize("path", [
    f"{API}/regions/{BAD}/", f"{API}/offices/{BAD}/", f"{API}/employees/{BAD}/",
    f"{API}/work-schedules/{BAD}/", f"{API}/departments/{BAD}/", f"{API}/positions/{BAD}/",
    f"{API}/employees/{BAD}/assignments/", f"{API}/employees/{BAD}/photo/",
])
def test_malformed_pk_is_not_found_not_500(hr, path):
    assert hr.get(path).status_code == 404


def test_malformed_document_id_is_not_found(hr, employee):
    response = hr.get(f"{API}/employees/{employee.id}/documents/{BAD}/download/")
    assert response.status_code == 404


@pytest.mark.parametrize("name", ["office_id", "region_id", "department_id", "position_id"])
@pytest.mark.parametrize("endpoint", ["", "counts/", "highlights/"])
def test_malformed_employee_filters_are_400(hr, office, name, endpoint):
    response = hr.get(f"{API}/employees/{endpoint}", {name: f"{office.id},{BAD}"})
    assert response.status_code == 400, response.content


def test_malformed_office_region_filter_is_400(hr):
    assert hr.get(f"{API}/offices/", {"region_id": BAD}).status_code == 400


# --- даты, числа, NUL ------------------------------------------------------------


@pytest.mark.parametrize("at", ["0001-01-01", "9999-12-31"])
@pytest.mark.parametrize("endpoint", ["", "counts/", "highlights/"])
def test_extreme_at_is_400(hr, employee, at, endpoint):
    assert hr.get(f"{API}/employees/{endpoint}", {"at": at}).status_code == 400


def test_ordinary_at_still_works(hr, employee):
    assert hr.get(f"{API}/employees/highlights/", {"at": "2026-09-25"}).status_code == 200


@pytest.mark.parametrize("offset", ["99999999999999999999999", "1000001"])
def test_huge_offset_is_400(hr, employee, offset):
    assert hr.get(f"{API}/employees/", {"offset": offset}).status_code == 400


@pytest.mark.parametrize("params", [
    {"search": "Ив\x00ан"}, {"status": "ACTIVE\x00"},
])
def test_nul_in_list_filters_is_not_500(hr, employee, params):
    assert hr.get(f"{API}/employees/", params).status_code in (200, 400)


def test_nul_in_global_search_is_not_500(hr, employee):
    # 400 даёт общий фильтр NUL в query (зона generic, core/api.py);
    # сервис дополнительно вычищает NUL сам — для вызовов не через HTTP.
    assert hr.get(f"{API}/employees/search/", {"q": "Ив\x00ан"}).status_code in (200, 400)


# --- поля карточки ---------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"gender": "X" * 15},
    {"marital_status": "Y" * 20},
    {"gender": "ALIEN"},
    {"preferred_language": "<b>x</b>"},
    {"birth_date": "0001-01-01"},
    {"birth_date": "2999-01-01"},
    {"first_name": "Ив\x00ан"},
])
def test_bad_card_fields_are_400(hr, employee, body):
    response = hr.patch(f"{API}/employees/{employee.id}/", body, format="json")
    assert response.status_code == 400, response.content


@pytest.mark.parametrize("body", [
    {"gender": "MALE", "marital_status": "SINGLE"},
    {"gender": "", "marital_status": None},
    {"preferred_language": "uz"},
    {"birth_date": "1990-05-01"},
])
def test_good_card_fields_pass(hr, employee, body):
    response = hr.patch(f"{API}/employees/{employee.id}/", body, format="json")
    assert response.status_code == 200, response.content


# --- офисы: координаты -----------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"latitude": "500.0", "longitude": "10.0"},
    {"latitude": "40.0", "longitude": "-181.0"},
    {"latitude": "40.0"},
    {"geofence_radius_m": 0},
])
def test_office_create_rejects_bad_location(hr, region, body):
    response = hr.post(f"{API}/offices/", {"region_id": str(region.id), **body}, format="json")
    assert response.status_code == 400, response.content


def test_office_create_with_good_location(hr, region):
    body = {"region_id": str(region.id), "latitude": "38.5598", "longitude": "68.7870",
            "geofence_radius_m": 150}
    assert hr.post(f"{API}/offices/", body, format="json").status_code == 201


def test_office_update_rejects_latitude_out_of_range(hr, office):
    body = {"latitude": "91.0", "longitude": "10.0"}
    assert hr.patch(f"{API}/offices/{office.id}/", body, format="json").status_code == 400


# --- документы сотрудника --------------------------------------------------------


def test_attach_document_without_title_is_not_500(hr, employee, organization, private_files):
    """Прежде `stored.original_name` → AttributeError → 500."""
    paper = store(
        SimpleUploadedFile("contract.pdf", PDF_MIN, content_type="application/pdf"),
        organization_id=organization.id, employee=None, allowed_types=("application/pdf",),
        max_bytes=1024 * 1024, prefix="employees/documents",
    ).file
    response = hr.post(f"{API}/employees/{employee.id}/documents/",
                       {"kind": "OTHER", "file_id": str(paper.id)}, format="json")
    assert response.status_code == 201, response.content
    assert response.json()["title"] == "contract.pdf"


def test_attach_document_with_unknown_kind_is_400(hr, employee, organization, private_files):
    document = attach_papers(organization, employee)
    response = hr.post(f"{API}/employees/{employee.id}/documents/",
                       {"kind": "BOGUS", "file_id": str(document.file_id)}, format="json")
    assert response.status_code == 400


def test_foreign_purpose_file_cannot_be_attached(hr, employee, organization, private_files):
    """Справка больничного (префикс absences/) не становится «документом»."""
    sick_note = store(
        SimpleUploadedFile("spravka.png", PNG_1X1, content_type="image/png"),
        organization_id=organization.id, employee=employee, allowed_types=("image/png",),
        max_bytes=1024 * 1024, prefix="absences",
    ).file
    response = hr.post(f"{API}/employees/{employee.id}/photo-set/",
                       {"file_id": str(sick_note.id)}, format="json")
    assert response.status_code == 400


# --- приём: привязка Telegram ----------------------------------------------------


def test_onboarding_without_telegram_manage_does_not_bind(
    make_actor, organization, office, department, position, work_schedule, telegram_settings,
):
    actor = make_actor(organization, permissions=(
        "employees.read", "employees.manage", "schedules.read", "schedules.manage",
    ))
    made = EmployeeOnboardingService().onboard(
        actor, **form(office, department, position, work_schedule, telegram_user_id=777_000_999),
    )
    assert made.created is True
    assert made.telegram.state == "SKIPPED"
    assert not TelegramAccount.objects.filter(telegram_user_id=777_000_999).exists()


# --- заблокированная учётка -------------------------------------------------------


@pytest.mark.parametrize("change", [{"status": "LOCKED"}, {"status": "ARCHIVED"}, {"archived": True}])
def test_inactive_user_has_no_permissions_or_scope(make_user, organization, change):
    from django.utils import timezone

    from humotech.core.rbac import Actor

    user = make_user(organization, permissions=HR_FULL_PERMISSIONS)
    actor = Actor.from_user(user)
    assert "employees.read" in AccessControl().permissions(actor)

    if change.get("archived"):
        user.archived_at = timezone.now()
    else:
        user.status = change["status"]
    user.save()

    access = AccessControl()
    assert access.permissions(actor) == set()
    assert access.scope(actor).sees_nothing
    assert EmployeeAttachmentService().access.has(actor, "employees.read") is False


def test_unknown_uuid_is_plain_not_found(hr):
    assert hr.get(f"{API}/employees/{uuid.uuid4()}/").status_code == 404
