"""REST API кадровой части: вход, справочники, сотрудники, графики.

Проверяется вся дорога целиком — маршрут, сериализатор, сервис, ORM, база.
Тесты сервисов лежат отдельно; здесь важно, что HTTP-слой ничего не теряет
и не добавляет: права те же, изоляция организаций та же, ошибки в том же виде.
"""

from __future__ import annotations

from datetime import date

import pytest

from django_tests.conftest import HR_FULL_PERMISSIONS, HR_READONLY_PERMISSIONS

pytestmark = pytest.mark.django_db

API = "/api/v1"


@pytest.fixture()
def hr_user(make_user, organization):
    return make_user(organization, permissions=HR_FULL_PERMISSIONS,
                     raw_password="Правильный-Пароль-2026")


@pytest.fixture()
def hr_client(api_client, hr_user):
    api_client.force_authenticate(user=hr_user)
    return api_client


# --- вход ------------------------------------------------------------------

def test_login_requires_organization_email_and_password(
    api_client, organization, hr_user
):
    """Почта уникальна ВНУТРИ организации, поэтому одной пары «почта + пароль»
    для опознания недостаточно."""
    response = api_client.post(f"{API}/auth/login", {
        "organization_code": organization.code,
        "email": hr_user.email,
        "password": "Правильный-Пароль-2026",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == hr_user.email
    assert body["organization_code"] == organization.code
    assert "regions.manage" in body["permissions"]


def test_login_with_wrong_password_is_refused(api_client, organization, hr_user):
    response = api_client.post(f"{API}/auth/login", {
        "organization_code": organization.code,
        "email": hr_user.email,
        "password": "не тот пароль",
    })
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_login_from_another_organization_is_refused(
    api_client, other_organization, hr_user
):
    """Тот же адрес и пароль, но другая организация — вход не проходит."""
    response = api_client.post(f"{API}/auth/login", {
        "organization_code": other_organization.code,
        "email": hr_user.email,
        "password": "Правильный-Пароль-2026",
    })
    assert response.status_code == 401


def test_anonymous_request_is_refused(api_client):
    assert api_client.get(f"{API}/regions/").status_code in (401, 403)


def test_current_user_reports_roles_and_permissions(hr_client, hr_user):
    body = hr_client.get(f"{API}/auth/me").json()
    assert body["id"] == str(hr_user.id)
    assert set(body["permissions"]) >= {"employees.manage", "offices.read"}


# --- регионы и офисы -------------------------------------------------------

def test_region_lifecycle_through_api(hr_client):
    created = hr_client.post(f"{API}/regions/", {
        "code": "sughd", "name": "Согд", "timezone": "Asia/Dushanbe",
    })
    assert created.status_code == 201
    region = created.json()
    assert region["code"] == "SUGHD"

    renamed = hr_client.patch(f"{API}/regions/{region['id']}/", {"name": "Согд-2"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Согд-2"

    off = hr_client.post(f"{API}/regions/{region['id']}/deactivate/")
    assert off.status_code == 200 and off.json()["status"] == "INACTIVE"

    on = hr_client.post(f"{API}/regions/{region['id']}/reactivate/")
    assert on.status_code == 200 and on.json()["status"] == "ACTIVE"

    listed = hr_client.get(f"{API}/regions/").json()
    assert listed["has_more"] is False
    assert any(item["id"] == region["id"] for item in listed["items"])


def test_office_list_shows_region_and_supports_filters(hr_client, region):
    for index in range(3):
        hr_client.post(f"{API}/offices/", {
            "region_id": str(region.id), "code": f"OF{index}",
            "name": f"Офис {index}", "address": f"ул. Тестовая, {index}",
            "timezone": "Asia/Dushanbe",
        })

    body = hr_client.get(f"{API}/offices/").json()
    assert len(body["items"]) == 3
    assert body["items"][0]["region_code"] == region.code
    assert body["items"][0]["region_name"] == region.name

    assert len(hr_client.get(f"{API}/offices/?search=Офис 1").json()["items"]) == 1
    assert len(
        hr_client.get(f"{API}/offices/?region_id={region.id}").json()["items"]
    ) == 3


def test_office_pagination_walks_every_row_once(hr_client, region):
    for index in range(5):
        hr_client.post(f"{API}/offices/", {
            "region_id": str(region.id), "code": f"P{index}", "name": f"Офис {index}",
            "address": "адрес", "timezone": "Asia/Dushanbe",
        })

    seen, cursor, pages = [], None, 0
    while True:
        url = f"{API}/offices/?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = hr_client.get(url).json()
        seen.extend(item["id"] for item in body["items"])
        pages += 1
        if not body["next_cursor"]:
            break
        cursor = body["next_cursor"]
        assert pages < 10
    assert len(seen) == 5 and len(set(seen)) == 5


def test_broken_cursor_gives_validation_error(hr_client):
    response = hr_client.get(f"{API}/offices/?cursor=не-курсор")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# --- сотрудники ------------------------------------------------------------

def _create_employee(client, office, **overrides) -> dict:
    payload = {
        "employee_number": "EMP-1000",
        "first_name": "Пётр", "last_name": "Петров",
        "hire_date": "2025-01-15",
        "office_id": str(office.id),
        "phone": "+992 900 11 22 33",
        "corporate_email": "p.petrov@humotech.tj",
    }
    payload.update(overrides)
    response = client.post(f"{API}/employees/", payload)
    assert response.status_code == 201, response.json()
    return response.json()


def test_employee_lifecycle_through_api(hr_client, office, other_office):
    card = _create_employee(hr_client, office)
    assert card["full_name"] == "Петров Пётр"
    assert card["current_assignment"]["office_id"] == str(office.id)
    assert card["telegram"]["connected"] is False

    employee_id = card["id"]

    listed = hr_client.get(f"{API}/employees/?at=2025-06-01").json()
    row = next(item for item in listed["items"] if item["id"] == employee_id)
    assert row["current_assignment"]["office_name"] == office.name
    assert row["current_assignment"]["region_name"] == "Душанбе"

    updated = hr_client.patch(f"{API}/employees/{employee_id}/",
                              {"phone": "+992 900 00 00 01"})
    assert updated.status_code == 200
    assert updated.json()["phone"] == "+992 900 00 00 01"

    moved = hr_client.post(f"{API}/employees/{employee_id}/change-assignment/", {
        "effective_from": "2025-03-01", "office_id": str(other_office.id),
    })
    assert moved.status_code == 201
    assert moved.json()["office_id"] == str(other_office.id)

    history = hr_client.get(f"{API}/employees/{employee_id}/assignments/").json()
    assert len(history["items"]) == 2, "прежний период сохранён"
    assert history["items"][1]["valid_to"] == "2025-02-28", (
        "старый период закрыт днём раньше начала нового"
    )

    # карточка «на дату» показывает состояние того периода
    before = hr_client.get(
        f"{API}/employees/{employee_id}/?at=2025-02-01"
    ).json()
    assert before["current_assignment"]["office_id"] == str(office.id)

    suspended = hr_client.post(f"{API}/employees/{employee_id}/deactivate/")
    assert suspended.json()["employment_status"] == "SUSPENDED"
    assert hr_client.post(
        f"{API}/employees/{employee_id}/reactivate/"
    ).json()["employment_status"] == "ACTIVE"

    terminated = hr_client.post(f"{API}/employees/{employee_id}/terminate/",
                                {"termination_date": "2025-08-31"})
    assert terminated.status_code == 200
    body = terminated.json()
    assert body["employment_status"] == "TERMINATED"
    assert body["termination_date"] == "2025-08-31"
    # история не тронута
    assert len(body["assignment_history"]) == 2


def test_employee_validation_errors_have_stable_codes(hr_client, office):
    response = hr_client.post(f"{API}/employees/", {
        "employee_number": "EMP-2", "first_name": "Имя", "last_name": "Фамилия",
        "hire_date": "2025-01-15", "office_id": str(office.id),
        "phone": "позвоните мне",
    })
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "телефона" in body["error"]["message"]


def test_duplicate_employee_number_gives_conflict(hr_client, office):
    _create_employee(hr_client, office)
    response = hr_client.post(f"{API}/employees/", {
        "employee_number": "EMP-1000", "first_name": "Другой",
        "last_name": "Человек", "hire_date": "2025-01-15",
        "office_id": str(office.id),
    })
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"]["constraint"] == "uq_employees_org_number"


def test_bad_date_parameter_is_rejected(hr_client):
    response = hr_client.get(f"{API}/employees/?at=вчера")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# --- графики ---------------------------------------------------------------

def test_schedule_create_and_assign_through_api(hr_client, office):
    card = _create_employee(hr_client, office)

    created = hr_client.post(f"{API}/work-schedules/", {
        "name": "Пятидневка", "timezone": "Asia/Dushanbe",
        "weekly_minutes": 2400,
        "days": [
            {"weekday": 1, "is_working_day": True,
             "start_time": "09:00:00", "end_time": "18:00:00",
             "breaks": [{"name": "Обед", "start_time": "13:00:00",
                         "end_time": "14:00:00"}]},
            {"weekday": 6, "is_working_day": False},
        ],
    })
    assert created.status_code == 201, created.json()
    schedule = created.json()
    assert [day["weekday"] for day in schedule["days"]] == [1, 6]
    assert schedule["days"][0]["breaks"][0]["name"] == "Обед"

    assigned = hr_client.post(f"{API}/work-schedules/{schedule['id']}/assign/", {
        "employee_id": card["id"], "valid_from": "2025-02-01",
    })
    assert assigned.status_code == 201
    assert assigned.json()["schedule_id"] == schedule["id"]

    history = hr_client.get(f"{API}/employees/{card['id']}/schedules").json()
    assert len(history["items"]) == 1
    assert history["items"][0]["schedule_name"] == "Пятидневка"

    card_now = hr_client.get(f"{API}/employees/{card['id']}/?at=2025-06-01").json()
    assert card_now["current_schedule"]["name"] == "Пятидневка"


def test_night_shift_needs_explicit_flag_through_api(hr_client):
    response = hr_client.post(f"{API}/work-schedules/", {
        "name": "Ночная", "timezone": "Asia/Dushanbe", "weekly_minutes": 2400,
        "days": [{"weekday": 1, "is_working_day": True,
                  "start_time": "22:00:00", "end_time": "06:00:00"}],
    })
    assert response.status_code == 400
    assert "раньше его начала" in response.json()["error"]["message"]

    ok = hr_client.post(f"{API}/work-schedules/", {
        "name": "Ночная", "timezone": "Asia/Dushanbe", "weekly_minutes": 2400,
        "days": [{"weekday": 1, "is_working_day": True,
                  "start_time": "22:00:00", "end_time": "06:00:00",
                  "crosses_midnight": True}],
    })
    assert ok.status_code == 201


# --- права и изоляция через HTTP -------------------------------------------

def test_readonly_user_is_refused_on_writes(api_client, make_user, organization,
                                            region, office):
    user = make_user(organization, permissions=HR_READONLY_PERMISSIONS)
    api_client.force_authenticate(user=user)

    assert api_client.get(f"{API}/regions/").status_code == 200

    for method, url, payload in (
        ("post", f"{API}/regions/", {"code": "X", "name": "X"}),
        ("patch", f"{API}/regions/{region.id}/", {"name": "X"}),
        ("post", f"{API}/regions/{region.id}/deactivate/", {}),
        ("post", f"{API}/offices/{office.id}/deactivate/", {}),
    ):
        response = getattr(api_client, method)(url, payload)
        assert response.status_code == 403, url
        assert response.json()["error"]["code"] == "forbidden"


def test_user_without_permissions_cannot_read(api_client, make_user, organization):
    api_client.force_authenticate(user=make_user(organization, permissions=()))
    response = api_client.get(f"{API}/employees/")
    assert response.status_code == 403
    assert "employees.read" in response.json()["error"]["message"]


def test_other_organization_objects_answer_as_missing(
    api_client, make_user, other_organization, region, office, employee
):
    """Чужой объект отвечает как несуществующий: иначе перебором
    идентификаторов можно пересчитать записи соседей."""
    api_client.force_authenticate(
        user=make_user(other_organization, permissions=HR_FULL_PERMISSIONS)
    )
    for url in (f"{API}/regions/{region.id}/",
                f"{API}/offices/{office.id}/",
                f"{API}/employees/{employee.id}/"):
        response = api_client.get(url)
        assert response.status_code == 404, url
        assert response.json()["error"]["code"] == "not_found"


def test_regional_scope_limits_the_list_over_http(
    api_client, make_user, organization, region, office, other_office, hr_client
):
    mine = _create_employee(hr_client, office, employee_number="R-1")
    theirs = _create_employee(hr_client, other_office, employee_number="R-2")

    api_client.force_authenticate(
        user=make_user(organization, permissions=("employees.read",), region=region)
    )
    body = api_client.get(f"{API}/employees/?at=2025-06-01").json()
    ids = {item["id"] for item in body["items"]}
    assert mine["id"] in ids
    assert theirs["id"] not in ids

    denied = api_client.get(f"{API}/employees/{theirs['id']}/")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
