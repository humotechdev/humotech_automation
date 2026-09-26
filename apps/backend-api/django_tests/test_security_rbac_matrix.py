"""Матрица межорганизационного доступа по маршрутам API (зона rbac).

Пользователь организации A со ВСЕМИ разрешениями и областью на всю свою
организацию обращается к объектам организации B по каждому маршруту с
идентификатором в пути. Ожидается 404 (для справочников и кадровых
маршрутов — ровно 404: 403 подтвердил бы существование чужой записи),
для маршрутов чужих зон — 403 или 404. Состояние базы до и после
одинаково: счётчики всех таблиц и отпечаток строк организации B.

Каждый случай — положительный контроль: HR самой организации B тем же
запросом НЕ получает 403/404. Иначе «отказ» мог бы значить просто
неверный адрес или тело.

Полнота: все маршруты `api/v1/` с параметром в пути либо проверяются
здесь, либо перечислены в SKIPPED с причиной.
"""

from __future__ import annotations

import pytest
from django.urls import URLResolver, get_resolver, reverse

from django_tests.security_rbac_world import (
    add_absence_request,
    add_invitation,
    build_org_world,
    client_for,
    db_state,
    diff_state,
    new_org,
)
from django_tests.conftest import create_actor, link_telegram
from humotech.core.permissions_catalog import ALL_PERMISSION_CODES

pytestmark = pytest.mark.django_db

D = "2030-01-01"

# Маршруты, где объект — справочник или кадровая запись зоны rbac.
MINE_PREFIXES = (
    "region-", "office-", "employee-", "work-schedule-", "department-", "position-",
)


def _emp(w):
    return {"employee_pk": w["employee"].id}


def _pk(key):
    return lambda w: {"pk": w[key].id}


# имя маршрута -> (параметры пути, {метод: тело})
ROUTES = {
    "employee-schedules": (_emp, {"get": None}),
    "employee-telegram": (_emp, {"get": None}),
    "employee-telegram-disconnect": (_emp, {"post": {}}),
    "role-detail": (lambda w: {"role_id": w["role"].id},
                    {"get": None, "patch": {"name": "Взлом"}}),
    "user-grants": (lambda w: {"user_id": w["owner"].id}, {"get": None}),
    "grant-detail": (lambda w: {"grant_id": w["grant"].id},
                     {"patch": {"valid_to_date": "2031-01-01"}, "delete": None}),
    "absence-request": (lambda w: {"request_id": w["absence_request"].id}, {"get": None}),
    "absence-request-decision": (
        lambda w: {"request_id": w["absence_request"].id, "decision": "reject"},
        {"post": {"comment": "нет"}}),
    "region-detail": (_pk("region"), {"get": None, "patch": {"name": "Взлом"}}),
    "region-deactivate": (_pk("region"), {"post": {}}),
    "region-reactivate": (_pk("region"), {"post": {}}),
    "office-detail": (_pk("office"), {"get": None, "patch": {"name": "Взлом"}}),
    "office-close": (_pk("office"), {"post": {}}),
    "office-deactivate": (_pk("office"), {"post": {}}),
    "office-reactivate": (_pk("office"), {"post": {}}),
    "employee-detail": (_pk("employee"), {"get": None, "patch": {"first_name": "Взлом"}}),
    "employee-assignments": (_pk("employee"), {"get": None}),
    "employee-change-assignment": (
        _pk("employee"), {"post": lambda w: {"effective_from": D, "office_id": str(w["office"].id)}}),
    "employee-deactivate": (_pk("employee"), {"post": {}}),
    "employee-reactivate": (_pk("employee"), {"post": {}}),
    "employee-document-attach": (
        _pk("employee"),
        {"post": lambda w: {"kind": "OTHER", "file_id": str(w["document"].file_id)}}),
    "employee-document-detach": (
        lambda w: {"pk": w["employee"].id, "document_id": w["document"].id}, {"delete": None}),
    "employee-document-download": (
        lambda w: {"pk": w["employee"].id, "document_id": w["document"].id}, {"get": None}),
    "employee-end-probation": (_pk("employee"), {"post": {}}),
    "employee-photo": (_pk("employee"), {"get": None}),
    "employee-photo-set": (
        _pk("employee"), {"post": lambda w: {"file_id": str(w["employee"].photo_id)}}),
    "employee-promote": (_pk("employee"), {"post": {}}),
    "employee-terminate": (_pk("employee"), {"post": {"termination_date": D}}),
    "work-schedule-detail": (
        _pk("schedule"), {"get": None, "patch": {"name": "Взлом"}, "delete": None}),
    "work-schedule-assign": (
        _pk("schedule"),
        {"post": lambda w: {"employee_id": str(w["employee"].id), "valid_from": D}}),
    "work-schedule-assign-department": (
        _pk("schedule"),
        {"post": lambda w: {"department_id": str(w["department"].id), "valid_from": D}}),
    "work-schedule-deactivate": (_pk("schedule"), {"post": {}}),
    "work-schedule-reactivate": (_pk("schedule"), {"post": {}}),
    "qr-point-detail": (_pk("qr_point"), {"get": None, "patch": {"name": "Взлом"}, "delete": None}),
    "qr-point-activate": (_pk("qr_point"), {"post": {}}),
    "qr-point-deactivate": (_pk("qr_point"), {"post": {}}),
    "qr-point-reissue-token": (_pk("qr_point"), {"post": {}}),
    "qr-point-sticker": (_pk("qr_point"), {"get": None}),
    "department-detail": (
        _pk("department"), {"get": None, "patch": {"name": "Взлом"}, "delete": None}),
    "department-deactivate": (_pk("department"), {"post": {}}),
    "department-reactivate": (_pk("department"), {"post": {}}),
    "position-detail": (
        _pk("position"), {"get": None, "patch": {"name": "Взлом"}, "delete": None}),
    "position-deactivate": (_pk("position"), {"post": {}}),
    "position-reactivate": (_pk("position"), {"post": {}}),
    "absence-type-detail": (
        _pk("absence_type"), {"get": None, "patch": {"name": "Взлом"}, "delete": None}),
    "absence-type-deactivate": (_pk("absence_type"), {"post": {}}),
    "absence-type-reactivate": (_pk("absence_type"), {"post": {}}),
    "crm-user-detail": (_pk("owner"), {"get": None, "patch": {"full_name": "Взлом"}}),
    "crm-user-activate": (_pk("owner"), {"post": {}}),
    "crm-user-deactivate": (_pk("owner"), {"post": {}}),
    "crm-user-set-password": (_pk("owner"), {"post": {"password": "Vzlom-Parol-2030!x"}}),
    "calendar-exception-detail": (
        _pk("calendar"), {"get": None, "patch": {"name": "Взлом"}, "delete": None}),
    "calendar-exception-deactivate": (_pk("calendar"), {"post": {}}),
    "calendar-exception-reactivate": (_pk("calendar"), {"post": {}}),
    "telegram-invitation-confirm": (_pk("invitation"), {"post": {}}),
    "telegram-invitation-reject": (_pk("invitation"), {"post": {}}),
    "telegram-invitation-revoke": (_pk("invitation"), {"post": {}}),
}

_OTHER_ZONE = "объект чужой зоны без дешёвого построителя; межорганизационный доступ — в test_security_<зона>.py"

# Имя маршрута (или префикс с '*') -> причина, по которой он не в матрице.
SKIPPED = {
    "self-*": "маршруты сотрудника /me/*: вход по Bearer Mini App, организация и сотрудник берутся из токена",
    "me-absence-paper": "маршрут сотрудника /me/*: вход по Bearer Mini App",
    "setting-detail": "ключ настройки, а не идентификатор; организация берётся из сессии",
    "notification-feed-item": "составной ключ события; лента строится по области пользователя (зона outbox)",
    "report-export": "вид отчёта из каталога, а не идентификатор (зона reports)",
    "policy-version*": _OTHER_ZONE + " (surveys/onboarding)",
    "employee-onboarding*": (
        "контроль требует программы ознакомления (зона surveys/onboarding); "
        "сотрудник проверяется require_visible_employee в onboarding/services.py"),
    "qr-device-action": _OTHER_ZONE + " (attendance)",
    "attendance-correction-decision": _OTHER_ZONE + " (attendance)",
    "absence-request-application*": _OTHER_ZONE + " (absences)",
    "absence-request-period": _OTHER_ZONE + " (absences)",
    "absence-request-document-*": _OTHER_ZONE + " (absences)",
    "notification-*": _OTHER_ZONE + " (outbox)",
    "knowledge-*": _OTHER_ZONE + " (ai)",
    "unanswered-question-*": _OTHER_ZONE + " (questions)",
    "escalation-*": _OTHER_ZONE + " (questions)",
    "export-job-*": _OTHER_ZONE + " (reports)",
    "report-template-detail": _OTHER_ZONE + " (reports)",
    "survey-*": _OTHER_ZONE + " (surveys)",
    "onboarding-section-*": _OTHER_ZONE + " (surveys/onboarding)",
    "policy-document-*": _OTHER_ZONE + " (surveys/onboarding)",
    "policy-category-*": _OTHER_ZONE + " (surveys/onboarding)",
}

CASES = [(name, method) for name, (_, methods) in ROUTES.items() for method in methods]


def _mine(name: str) -> bool:
    return name.startswith(MINE_PREFIXES)


def _request(client, method, url, body):
    call = getattr(client, method)
    if body is None:
        return call(url)
    return call(url, body, format="json")


@pytest.fixture()
def world(settings, tmp_path, telegram_settings):
    settings.FILES = {**settings.FILES, "PRIVATE_ROOT": str(tmp_path), "SCANNER_ENABLED": False}
    w = build_org_world("B")
    add_invitation(w)
    add_absence_request(w)
    link_telegram(w["employee"], telegram_user_id=555_000_111)
    attacker_org = new_org("A")
    w["attacker"] = create_actor(attacker_org, permissions=ALL_PERMISSION_CODES)[0]
    return w


@pytest.mark.parametrize("name,method", CASES, ids=[f"{n}:{m}" for n, m in CASES])
def test_cross_org_access_is_not_found(world, name, method):
    params, methods = ROUTES[name]
    body = methods[method]
    body = body(world) if callable(body) else body
    url = reverse(f"v1:{name}", kwargs=params(world))

    before = db_state(world["org"].id)
    response = _request(client_for(world["attacker"]), method, url, body)
    after = db_state(world["org"].id)

    allowed = {404} if _mine(name) else {403, 404}
    assert response.status_code in allowed, (
        f"{method.upper()} {url} чужой организации: {response.status_code} "
        f"{getattr(response, 'content', b'')[:300]!r}"
    )
    assert diff_state(before, after) == []

    control = _request(client_for(world["owner"]), method, url, body)
    assert control.status_code not in (401, 403, 404, 405) and control.status_code < 500, (
        f"положительный контроль {method.upper()} {url}: {control.status_code} "
        f"{getattr(control, 'content', b'')[:300]!r}"
    )


# --- полнота матрицы -------------------------------------------------------------


def _walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern))
        else:
            yield prefix + str(p.pattern), p


def _skipped(name: str) -> bool:
    for key in SKIPPED:
        if key.endswith("*") and name.startswith(key[:-1]) or key == name:
            return True
    return False


def _methods(pattern) -> set[str]:
    actions = getattr(pattern.callback, "actions", None)
    if actions:
        # HEAD DRF добавляет к GET сам — отдельной логики за ним нет
        return set(actions) - {"head"}
    view = getattr(pattern.callback, "view_class", None)
    return {m for m in ("get", "post", "patch", "put", "delete") if hasattr(view, m)}


def test_every_parameterized_route_is_covered_or_skipped():
    missing, partial = [], []
    for route, pattern in _walk(get_resolver().url_patterns):
        if not route.startswith("api/v1/") or "format" in route:
            continue
        if "<" not in route and "(?P<" not in route:
            continue
        name = pattern.name
        if name in ROUTES:
            covered = set(ROUTES[name][1])
            if covered != _methods(pattern):
                partial.append((name, sorted(_methods(pattern) - covered)))
        elif not _skipped(name):
            missing.append(name)
    assert missing == [] and partial == [], (missing, partial)
