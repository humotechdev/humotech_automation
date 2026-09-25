"""Доступ вне области видимости внутри своей организации (зона rbac).

Роли REGIONAL_HR, OFFICE_ADMIN, MANAGER, VIEWER — с разрешениями из
`ROLE_PERMISSIONS` и областью «регион R1» или «офис O1». Плюс «HR_ADMIN
с областью R1» — полный набор кадровых прав, но на один регион: самый
сильный из ограниченных. Цели — объекты региона R2. Ожидается 403 или
404, состояние базы не меняется. Положительный контроль — HR на всю
организацию тем же запросом НЕ получает 403/404.

Сотрудник R2 работал ТОЛЬКО в R2: `require_visible_employee` пускает,
если в область попадает любое прошлое назначение.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from django_tests.conftest import create_actor, link_telegram, make_office, make_qr_point, make_work_schedule
from django_tests.security_rbac_world import (
    attach_papers,
    client_for,
    db_state,
    diff_state,
    new_employee,
    new_org,
    role_actor,
)
from humotech.core.errors import PermissionDenied
from humotech.core.permissions_catalog import ALL_PERMISSION_CODES, ROLE_PERMISSIONS
from humotech.departments.models import Department
from humotech.regions.models import Region
from humotech.schedules.models import CalendarException, EmployeeScheduleAssignment
from humotech.schedules.services import WorkScheduleService

pytestmark = pytest.mark.django_db

D = "2030-01-01"
ROLES = ("REGIONAL_HR", "OFFICE_ADMIN", "MANAGER", "VIEWER", "HR_ADMIN@R1")


@pytest.fixture()
def world(settings, tmp_path, telegram_settings):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path), "SCANNER_ENABLED": False}
    org = new_org("S")
    r1 = Region.objects.create(organization=org, code="R1", name="Север", status="ACTIVE")
    r2 = Region.objects.create(organization=org, code="R2", name="Юг", status="ACTIVE")
    o1, o2 = make_office(org, r1, "O1"), make_office(org, r2, "O2")
    dept2 = Department.objects.create(organization=org, office=o2, code="D2", name="Склад", status="ACTIVE")
    emp1 = new_employee(org, o1, "S-0001")
    emp2 = new_employee(org, o2, "S-0002", department=dept2)
    document = attach_papers(org, emp2)
    link_telegram(emp2, telegram_user_id=555_000_222)
    schedule = make_work_schedule(org)
    owner = create_actor(org, permissions=ALL_PERMISSION_CODES)[0]
    actors = {
        "REGIONAL_HR": role_actor(org, "REGIONAL_HR", region=r1),
        "OFFICE_ADMIN": role_actor(org, "OFFICE_ADMIN", office=o1),
        "MANAGER": role_actor(org, "MANAGER", office=o1),
        "VIEWER": role_actor(org, "VIEWER", region=r1),
        "HR_ADMIN@R1": role_actor(org, "HR_ADMIN", region=r1),
    }
    return {
        "org": org, "r1": r1, "r2": r2, "o1": o1, "o2": o2, "dept2": dept2,
        "emp1": emp1, "emp2": emp2, "document": document, "schedule": schedule,
        "owner": owner, "actors": actors, "qr_point": make_qr_point(org, o2),
        "calendar": CalendarException.objects.create(
            organization=org, office=o2, date=date(2030, 1, 7), name="Праздник",
            exception_type="HOLIDAY", is_working_day=False,
        ),
    }


def _pk(key):
    return lambda w: {"pk": w[key].id}


_EMP = _pk("emp2")

SCOPE_ROUTES = [
    ("region-detail", _pk("r2"), "get", None),
    ("region-detail", _pk("r2"), "patch", {"name": "Взлом"}),
    ("region-deactivate", _pk("r2"), "post", {}),
    ("office-detail", _pk("o2"), "get", None),
    ("office-detail", _pk("o2"), "patch", {"name": "Взлом"}),
    ("office-close", _pk("o2"), "post", {}),
    ("office-deactivate", _pk("o2"), "post", {}),
    ("employee-detail", _EMP, "get", None),
    ("employee-detail", _EMP, "patch", {"first_name": "Взлом"}),
    ("employee-assignments", _EMP, "get", None),
    ("employee-change-assignment", _EMP, "post", lambda w: {"effective_from": D, "office_id": str(w["o1"].id)}),
    ("employee-deactivate", _EMP, "post", {}),
    ("employee-terminate", _EMP, "post", {"termination_date": D}),
    ("employee-promote", _EMP, "post", {}),
    ("employee-end-probation", _EMP, "post", {}),
    ("employee-photo", _EMP, "get", None),
    ("employee-photo-set", _EMP, "post", lambda w: {"file_id": str(w["emp2"].photo_id)}),
    ("employee-document-attach", _EMP, "post",
     lambda w: {"kind": "OTHER", "file_id": str(w["document"].file_id), "title": "Копия"}),
    ("employee-document-detach", lambda w: {"pk": w["emp2"].id, "document_id": w["document"].id}, "delete", None),
    ("employee-document-download", lambda w: {"pk": w["emp2"].id, "document_id": w["document"].id}, "get", None),
    ("employee-schedules", lambda w: {"employee_pk": w["emp2"].id}, "get", None),
    ("employee-telegram", lambda w: {"employee_pk": w["emp2"].id}, "get", None),
    ("employee-telegram-disconnect", lambda w: {"employee_pk": w["emp2"].id}, "post", {}),
    ("work-schedule-assign", _pk("schedule"), "post",
     lambda w: {"employee_id": str(w["emp2"].id), "valid_from": D}),
    ("work-schedule-assign-department", _pk("schedule"), "post",
     lambda w: {"department_id": str(w["dept2"].id), "valid_from": D}),
    ("department-detail", _pk("dept2"), "get", None),
    ("department-detail", _pk("dept2"), "patch", {"name": "Взлом"}),
    ("department-deactivate", _pk("dept2"), "post", {}),
    ("qr-point-detail", _pk("qr_point"), "get", None),
    ("qr-point-detail", _pk("qr_point"), "patch", {"name": "Взлом"}),
    ("qr-point-reissue-token", _pk("qr_point"), "post", {}),
    ("qr-point-sticker", _pk("qr_point"), "get", None),
    ("calendar-exception-detail", _pk("calendar"), "get", None),
    ("calendar-exception-detail", _pk("calendar"), "patch", {"name": "Взлом"}),
    ("calendar-exception-detail", _pk("calendar"), "delete", None),
]

CASES = [(role, *route) for role in ROLES for route in SCOPE_ROUTES]


def _request(client, method, url, body):
    call = getattr(client, method)
    return call(url) if body is None else call(url, body, format="json")


@pytest.mark.parametrize(
    "role,name,params,method,body", CASES,
    ids=[f"{c[0]}:{c[1]}:{c[3]}" for c in CASES],
)
def test_out_of_scope_object_is_refused(world, role, name, params, method, body):
    body = body(world) if callable(body) else body
    url = reverse(f"v1:{name}", kwargs=params(world))

    before = db_state(world["org"].id)
    response = _request(client_for(world["actors"][role]), method, url, body)
    after = db_state(world["org"].id)

    assert response.status_code in (403, 404), (
        f"{role} {method.upper()} {url}: {response.status_code} {_text(response)}"
    )
    assert diff_state(before, after) == []

    control = _request(client_for(world["owner"]), method, url, body)
    assert control.status_code not in (401, 403, 404, 405) and control.status_code < 500, (
        f"контроль {method.upper()} {url}: {control.status_code} {_text(control)}"
    )


def _text(response):
    # у файлового ответа нет `content`: сам факт файла и есть утечка
    return getattr(response, "content", b"<file>")[:300]


# --- точечные сценарии графиков -------------------------------------------------


def test_schedule_history_of_out_of_scope_employee_is_denied(world):
    from humotech.core.rbac import Actor

    user = world["actors"]["REGIONAL_HR"]
    with pytest.raises(PermissionDenied):
        WorkScheduleService().history(Actor.from_user(user), world["emp2"].id)


def test_assign_to_org_wide_department_touches_only_visible_people(world):
    """Общий отдел (без офиса) с людьми из обоих регионов.

    Региональный HR назначает график всему отделу — получить его должны
    только люди его региона; сотрудник R2 остаётся без изменений, и
    ответ не должен частично проваливаться после уже сделанных записей.
    """
    org = world["org"]
    common = Department.objects.create(organization=org, office=None, code="ALL", name="Общий", status="ACTIVE")
    for emp in (world["emp1"], world["emp2"]):
        emp.assignments.update(department=common)

    client = client_for(world["actors"]["REGIONAL_HR"])
    url = reverse("v1:work-schedule-assign-department", kwargs={"pk": world["schedule"].id})
    response = client.post(url, {"department_id": str(common.id), "valid_from": D}, format="json")

    assert response.status_code == 200, response.content
    assert response.json()["assigned"] == [str(world["emp1"].id)]
    assert not EmployeeScheduleAssignment.objects.filter(employee=world["emp2"]).exists()


def test_body_substitution_foreign_employee_with_own_schedule(world):
    """Свой график, чужой сотрудник в теле: 404 и ни одной строки."""
    attacker_org = new_org("A")
    attacker = create_actor(attacker_org, permissions=ALL_PERMISSION_CODES)[0]
    own_schedule = make_work_schedule(attacker_org)
    url = reverse("v1:work-schedule-assign", kwargs={"pk": own_schedule.id})

    response = client_for(attacker).post(
        url, {"employee_id": str(world["emp2"].id), "valid_from": D}, format="json"
    )
    assert response.status_code == 404
    assert not EmployeeScheduleAssignment.objects.filter(employee=world["emp2"]).exists()


def test_role_catalog_matches_matrix():
    """Матрица проверяет ровно те роли, что заявлены в каталоге."""
    for role in ROLES:
        assert role.split("@")[0] in ROLE_PERMISSIONS
