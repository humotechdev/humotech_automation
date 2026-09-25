"""Безопасность входа в CRM: перебор паролей, сессии, CSRF, журнал, ввод.

Каждая группа сначала воспроизводит атаку так, как её сделал бы
злоумышленник, и проверяет, что она больше не проходит. Данные вымышленные.
"""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from humotech.accounts.models import AuthRateCounter
from humotech.audit.models import AuditLog
from humotech.core import throttling

pytestmark = pytest.mark.django_db

API = "/api/v1"
PASSWORD = "Вымышленный-Пароль-2026"


@pytest.fixture()
def hr_user(make_user, organization):
    return make_user(organization, permissions=("employees.read",),
                     raw_password=PASSWORD)


@pytest.fixture()
def admin_user(make_user, organization):
    return make_user(organization, permissions=("users.manage", "employees.read"),
                     raw_password="Админ-Вымышленный-2026")


def _login(client, organization, email, password, *, ip="198.51.100.7", **extra):
    return client.post(
        f"{API}/auth/login",
        {"organization_code": organization.code if hasattr(organization, "code")
         else organization, "email": email, "password": password},
        format="json",
        REMOTE_ADDR=ip,
        **extra,
    )


def _logged_in(user, organization, password=PASSWORD, ip="198.51.100.7"):
    client = APIClient()
    response = _login(client, organization, user.email, password, ip=ip)
    assert response.status_code == 200, response.content
    return client


# --- перебор паролей -------------------------------------------------------


def test_bruteforce_from_one_address_is_locked_after_five_failures(
    api_client, organization, hr_user
):
    """До исправления: 50 неверных паролей подряд — 50 ответов 401,
    а 51-й, верный, — 200. Предела не было вовсе."""
    for _ in range(5):
        response = _login(api_client, organization, hr_user.email, "не тот")
        assert response.status_code == 401

    locked = _login(api_client, organization, hr_user.email, "не тот")
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "too_many_attempts"
    assert int(locked["Retry-After"]) > 0

    # Верный пароль во время блокировки тоже не проверяется: иначе
    # блокировка ничего бы не значила — перебор просто шёл бы дальше.
    correct = _login(api_client, organization, hr_user.email, PASSWORD)
    assert correct.status_code == 429
    assert "sessionid" not in correct.cookies


def test_lock_is_per_account_and_address(api_client, organization, hr_user):
    """Злоумышленник не закрывает вход настоящему HR с его адреса."""
    for _ in range(6):
        _login(api_client, organization, hr_user.email, "не тот", ip="203.0.113.66")
    assert _login(api_client, organization, hr_user.email, PASSWORD,
                  ip="203.0.113.66").status_code == 429

    response = _login(APIClient(), organization, hr_user.email, PASSWORD,
                      ip="198.51.100.20")
    assert response.status_code == 200


def test_spoofed_forwarded_for_does_not_open_a_new_counter(
    api_client, organization, hr_user
):
    """Без доверенного прокси заголовок не читается вовсе."""
    for n in range(5):
        _login(api_client, organization, hr_user.email, "не тот",
               HTTP_X_FORWARDED_FOR=f"10.9.{n}.1")
    response = _login(api_client, organization, hr_user.email, PASSWORD,
                      HTTP_X_FORWARDED_FOR="10.9.99.1")
    assert response.status_code == 429


def test_spoofed_forwarded_for_behind_proxy_is_ignored(
    api_client, organization, hr_user, settings
):
    """За одним прокси учитывается только адрес, дописанный им самим."""
    settings.TRUSTED_PROXY_COUNT = 1
    for n in range(5):
        _login(api_client, organization, hr_user.email, "не тот",
               ip="172.18.0.2", HTTP_X_FORWARDED_FOR=f"10.9.{n}.1, 203.0.113.5")
    response = _login(api_client, organization, hr_user.email, PASSWORD,
                      ip="172.18.0.2",
                      HTTP_X_FORWARDED_FOR="10.9.99.1, 203.0.113.5")
    assert response.status_code == 429


def test_drf_throttles_do_not_trust_forwarded_for():
    """Встроенные пределы DRF считали по всей строке XFF от клиента."""
    assert settings.REST_FRAMEWORK["NUM_PROXIES"] == settings.TRUSTED_PROXY_COUNT


def test_distributed_bruteforce_locks_the_account(api_client, organization, hr_user):
    """Много адресов по одной учётке — у учётки свой, общий предел."""
    for n in range(20):
        response = _login(api_client, organization, hr_user.email, "не тот",
                          ip=f"192.0.2.{n + 1}")
        assert response.status_code == 401
    response = _login(api_client, organization, hr_user.email, PASSWORD,
                      ip="192.0.2.200")
    assert response.status_code == 429


def test_password_spraying_from_one_address_is_locked(
    api_client, organization, hr_user
):
    """Один частый пароль по многим почтам с одного адреса."""
    for n in range(30):
        response = _login(api_client, organization, f"user{n}@example.test",
                          "Qwerty123456")
        assert response.status_code == 401
    response = _login(api_client, organization, hr_user.email, PASSWORD)
    assert response.status_code == 429


def test_successful_logins_do_not_use_up_the_address_limit(
    organization, make_user
):
    """Весь офис за одним адресом входит без упора в предел адреса."""
    users = [make_user(organization, raw_password=PASSWORD) for _ in range(35)]
    for user in users:
        assert _login(APIClient(), organization, user.email, PASSWORD).status_code == 200


def test_case_variations_share_the_counter(api_client, organization, hr_user):
    """`iexact` находит ту же учётку — счётчик обязан быть тем же."""
    variants = [hr_user.email.upper(), hr_user.email.lower(), hr_user.email.title(),
                hr_user.email.swapcase(), hr_user.email.capitalize()]
    for variant in variants:
        _login(api_client, organization.code.lower(), variant, "не тот")
    assert _login(api_client, organization, hr_user.email, PASSWORD).status_code == 429


def test_unknown_account_is_locked_the_same_way(api_client, organization, hr_user):
    """Ответы для существующей и несуществующей учётки неразличимы."""
    ghost = "nobody-here@example.test"
    real, fake = [], []
    for _ in range(6):
        real.append(_login(api_client, organization, hr_user.email, "не тот"))
        fake.append(_login(api_client, organization, ghost, "не тот", ip="198.51.100.8"))
    assert [r.status_code for r in real] == [r.status_code for r in fake]
    assert [r.json()["error"]["code"] for r in real] == \
        [r.json()["error"]["code"] for r in fake]
    assert real[0].json() == fake[0].json()


def test_inactive_account_answers_like_a_wrong_password(
    api_client, organization, hr_user
):
    hr_user.status = "INACTIVE"
    hr_user.save(update_fields=["status"])
    inactive = _login(api_client, organization, hr_user.email, PASSWORD)
    missing = _login(api_client, organization, "ghost@example.test", PASSWORD)
    assert inactive.status_code == missing.status_code == 401
    assert inactive.json() == missing.json()


def test_success_resets_the_account_counter(api_client, organization, hr_user):
    for _ in range(4):
        _login(api_client, organization, hr_user.email, "не тот")
    assert _login(APIClient(), organization, hr_user.email, PASSWORD).status_code == 200
    for _ in range(4):
        assert _login(api_client, organization, hr_user.email, "не тот").status_code == 401


def test_lock_expires_by_itself(api_client, organization, hr_user):
    for _ in range(6):
        _login(api_client, organization, hr_user.email, "не тот")
    AuthRateCounter.objects.filter(locked_until__isnull=False).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )
    assert _login(api_client, organization, hr_user.email, PASSWORD).status_code == 200


def test_new_password_from_hr_lifts_the_account_lock(
    organization, hr_user, admin_user
):
    for n in range(21):
        _login(APIClient(), organization, hr_user.email, "не тот", ip=f"192.0.2.{n + 1}")
    admin = _logged_in(admin_user, organization, "Админ-Вымышленный-2026", ip="198.51.100.50")
    response = admin.post(f"{API}/users/{hr_user.id}/set-password/",
                          {"password": "Новый-Вымышленный-Пароль-7"}, format="json")
    assert response.status_code == 200, response.content
    assert _login(APIClient(), organization, hr_user.email,
                  "Новый-Вымышленный-Пароль-7", ip="192.0.2.230").status_code == 200


def test_locked_attempts_are_not_even_checked(api_client, organization, hr_user,
                                              monkeypatch):
    """Во время блокировки пароль не проверяется — перебор не идёт дальше."""
    for _ in range(5):
        _login(api_client, organization, hr_user.email, "не тот")
    calls = []
    import humotech.accounts.views as views

    real = views.authenticate
    monkeypatch.setattr(views, "authenticate",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(10):
        _login(api_client, organization, hr_user.email, PASSWORD)
    assert calls == []


@pytest.mark.django_db(transaction=True)
def test_counter_is_atomic_across_parallel_workers():
    """Параллельные запросы (разные процессы/воркеры, у каждого своё
    соединение с базой) не проскакивают предел: ровно 5 из 40."""
    key = throttling.counter_key("test", uuid.uuid4().hex)
    limit = throttling.Limit(hits=5, window_seconds=900, lock_seconds=900)
    barrier = threading.Barrier(8)

    def attempt(_):
        try:
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
            return throttling.hit(key, limit).allowed
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(40)))
    assert results.count(True) == 5
    AuthRateCounter.objects.filter(key=key).delete()


def test_counters_do_not_store_logins_in_clear(api_client, organization, hr_user):
    _login(api_client, organization, hr_user.email, "не тот")
    keys = list(AuthRateCounter.objects.values_list("key", flat=True))
    assert keys and all(len(key) == 64 for key in keys)
    assert not any(hr_user.email in key for key in keys)


# --- админка ---------------------------------------------------------------


def test_admin_login_form_cannot_be_used_for_bruteforce(client, organization, hr_user):
    """Форма входа админки не знает организации, и backend её не пускает
    ни с каким паролем: перебирать там нечего. Вход в админку — только
    через сессию CRM."""
    for password in ("не тот", PASSWORD):
        response = client.post("/admin/login/?next=/admin/",
                               {"username": hr_user.email, "password": password})
        assert response.status_code == 200
        assert "_auth_user_id" not in client.session


# --- сессии ----------------------------------------------------------------


def test_session_lifetime_is_twelve_hours_and_sliding(organization, hr_user):
    assert settings.SESSION_COOKIE_AGE == 12 * 60 * 60
    assert settings.SESSION_SAVE_EVERY_REQUEST is True
    client = _logged_in(hr_user, organization)
    cookie = client.cookies["sessionid"]
    assert int(cookie["max-age"]) == 12 * 60 * 60
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"

    session = Session.objects.get(session_key=cookie.value)
    Session.objects.filter(pk=session.pk).update(
        expire_date=timezone.now() + timedelta(hours=1)
    )
    assert client.get(f"{API}/auth/me").status_code == 200
    renewed = Session.objects.get(pk=session.pk).expire_date
    assert renewed > timezone.now() + timedelta(hours=11)


def test_login_rotates_session_key(organization, hr_user):
    """Фиксация сессии: ключ, выданный до входа, после входа не работает."""
    client = APIClient()
    client.get(f"{API}/auth/me")
    session = client.session
    session["planted"] = True
    session.save()
    planted = session.session_key
    client.cookies["sessionid"] = planted

    assert _login(client, organization, hr_user.email, PASSWORD).status_code == 200
    assert client.cookies["sessionid"].value != planted
    assert not Session.objects.filter(session_key=planted).exists()


def test_logout_kills_the_session_on_the_server(organization, hr_user):
    client = _logged_in(hr_user, organization)
    stolen = client.cookies["sessionid"].value
    assert client.post(f"{API}/auth/logout").status_code == 204
    assert not Session.objects.filter(session_key=stolen).exists()

    replay = APIClient()
    replay.cookies["sessionid"] = stolen
    assert replay.get(f"{API}/auth/me").status_code in (401, 403)


def test_deactivation_ends_open_sessions_for_good(organization, hr_user, admin_user):
    victim = _logged_in(hr_user, organization)
    stolen = victim.cookies["sessionid"].value
    admin = _logged_in(admin_user, organization, "Админ-Вымышленный-2026",
                       ip="198.51.100.50")

    assert admin.post(f"{API}/users/{hr_user.id}/deactivate/").status_code == 200
    assert victim.get(f"{API}/auth/me").status_code in (401, 403)
    assert not Session.objects.filter(session_key=stolen).exists()

    # Включили обратно — старая cookie не оживает.
    assert admin.post(f"{API}/users/{hr_user.id}/activate/").status_code == 200
    replay = APIClient()
    replay.cookies["sessionid"] = stolen
    assert replay.get(f"{API}/auth/me").status_code in (401, 403)


def test_inactive_user_session_is_refused_even_if_left_in_the_store(
    organization, hr_user
):
    """Статус сменили мимо сервиса (админка, скрипт) — сессия всё равно мертва."""
    client = _logged_in(hr_user, organization)
    type(hr_user).objects.filter(pk=hr_user.pk).update(status="LOCKED")
    assert client.get(f"{API}/auth/me").status_code in (401, 403)


def test_password_change_by_hr_ends_sessions(organization, hr_user, admin_user):
    victim = _logged_in(hr_user, organization)
    admin = _logged_in(admin_user, organization, "Админ-Вымышленный-2026",
                       ip="198.51.100.50")
    response = admin.post(f"{API}/users/{hr_user.id}/set-password/",
                          {"password": "Новый-Вымышленный-Пароль-7"}, format="json")
    assert response.status_code == 200
    assert victim.get(f"{API}/auth/me").status_code in (401, 403)
    # Себе администратор не отрубил.
    assert admin.get(f"{API}/auth/me").status_code == 200


def test_own_password_change_keeps_this_session_only(organization, admin_user):
    here = _logged_in(admin_user, organization, "Админ-Вымышленный-2026",
                      ip="198.51.100.50")
    elsewhere = _logged_in(admin_user, organization, "Админ-Вымышленный-2026",
                           ip="198.51.100.51")
    response = here.post(f"{API}/users/{admin_user.id}/set-password/",
                         {"password": "Сменил-Себе-Вымышленный-9"}, format="json")
    assert response.status_code == 200
    assert here.get(f"{API}/auth/me").status_code == 200
    assert elsewhere.get(f"{API}/auth/me").status_code in (401, 403)


# --- CSRF ------------------------------------------------------------------


def test_login_requires_csrf_token(organization, hr_user):
    """Login CSRF: чужая страница не может войти в браузере HR под своей
    учёткой. До исправления вход проходил без токена."""
    browser = APIClient(enforce_csrf_checks=True)
    response = _login(browser, organization, hr_user.email, PASSWORD)
    assert response.status_code == 403
    assert response.json()["error"]["message"].startswith("CSRF")

    # Так делает CRM: `GET /auth/me` при загрузке отдаёт cookie токена.
    assert browser.get(f"{API}/auth/me").status_code in (401, 403)
    token = browser.cookies["csrftoken"].value
    response = _login(browser, organization, hr_user.email, PASSWORD,
                      HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200


def test_mutations_and_logout_require_csrf(organization, hr_user):
    browser = APIClient(enforce_csrf_checks=True)
    browser.get(f"{API}/auth/me")
    token = browser.cookies["csrftoken"].value
    assert _login(browser, organization, hr_user.email, PASSWORD,
                  HTTP_X_CSRFTOKEN=token).status_code == 200

    assert browser.post(f"{API}/auth/logout").status_code == 403
    assert browser.post(f"{API}/regions/", {"code": "X", "name": "X"},
                        format="json").status_code == 403
    token = browser.cookies["csrftoken"].value  # новый после входа
    assert browser.post(f"{API}/auth/logout",
                        HTTP_X_CSRFTOKEN=token).status_code == 204


def test_csrf_cookie_is_readable_by_crm():
    assert settings.CSRF_COOKIE_HTTPONLY is False
    assert settings.CSRF_COOKIE_SAMESITE == "Lax"


# --- журнал ----------------------------------------------------------------


def test_login_events_are_audited(organization, hr_user):
    client = APIClient()
    _login(client, organization, hr_user.email, "не тот", HTTP_USER_AGENT="pytest-ua")
    _login(client, organization, "ghost@example.test", "не тот")
    _login(client, organization, hr_user.email, PASSWORD)
    client.post(f"{API}/auth/logout")

    rows = list(AuditLog.objects.filter(organization=organization,
                                        action__startswith="auth.")
                .order_by("occurred_at", "action"))
    actions = [row.action for row in rows]
    assert actions.count("auth.login_failed") == 2
    assert "auth.login" in actions and "auth.logout" in actions

    failed_real = next(r for r in rows if r.action == "auth.login_failed"
                       and r.entity_type == "users")
    assert failed_real.entity_id == hr_user.id
    assert failed_real.actor_user_id is None
    assert failed_real.ip_address == "198.51.100.7"
    assert failed_real.user_agent == "pytest-ua"
    failed_ghost = next(r for r in rows if r.action == "auth.login_failed"
                        and r.entity_type == "organizations")
    assert failed_ghost.entity_id == organization.id

    success = next(r for r in rows if r.action == "auth.login")
    assert success.actor_user_id == hr_user.id

    dump = json.dumps([[r.old_values, r.new_values] for r in rows], ensure_ascii=False)
    assert PASSWORD not in dump and "не тот" not in dump


def test_lock_is_audited_once(api_client, organization, hr_user):
    for _ in range(10):
        _login(api_client, organization, hr_user.email, "не тот")
    locks = AuditLog.objects.filter(organization=organization,
                                    action="auth.login_locked")
    assert locks.count() == 1
    assert locks.get().entity_id == hr_user.id


# --- ввод ------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    {"email": "a" * 100_000, "password": "x"},
    {"email": "a@b.c", "password": "x" * 1_000_000},
    {"email": {"$ne": ""}, "password": "x"},
    {"email": ["a@b.c"], "password": "x"},
    {"email": "a@b.c", "password": {"x": 1}},
    {"email": "a@b.c", "password": None},
    {"email": "a\x00@b.c", "password": "x"},
    {"email": "\ud800@b.c", "password": "x"},
    {"email": "a@b.c", "password": "\udfff"},
    {"email": "a@b.c"},
    {},
])
def test_hostile_login_input_is_a_400_not_a_500(api_client, organization, payload):
    payload = {"organization_code": organization.code, **payload}
    # json.dumps экранирует суррогаты (\ud800) — так их и шлёт атакующий.
    response = api_client.post(f"{API}/auth/login", json.dumps(payload),
                               content_type="application/json")
    assert response.status_code == 400, response.content


@pytest.mark.parametrize("email", [
    "админ@пример.тж", "ﬁ@ex.test", "Ａ@ex.test", "a​@ex.test",
    "🙂@ex.test", "a' OR '1'='1", "<script>alert(1)</script>",
])
def test_unusual_unicode_is_just_a_wrong_login(api_client, organization, email):
    response = _login(api_client, organization, email, "x")
    assert response.status_code == 401


def test_organization_code_of_wrong_type_or_size(api_client):
    for code in ({"a": 1}, "O" * 10_000, 12345):
        response = api_client.post(f"{API}/auth/login",
                                   {"organization_code": code, "email": "a",
                                    "password": "b"}, format="json")
        assert response.status_code in (400, 401)


def test_body_that_is_not_json(api_client):
    for body in ("{", "null", "[]", "\"строка\"", "1e999999"):
        response = api_client.post(f"{API}/auth/login", body,
                                   content_type="application/json")
        assert response.status_code == 400, (body, response.status_code)


def test_deeply_nested_json_is_not_a_500(api_client):
    body = "[" * 50_000 + "]" * 50_000
    response = api_client.post(f"{API}/auth/login", body,
                               content_type="application/json")
    assert response.status_code == 400


# --- управление учётками: нельзя трогать того, кто выше (передано от rbac) ---

from django_tests.conftest import create_actor  # noqa: E402
from humotech.accounts.models import User, UserRoleScope  # noqa: E402
from humotech.accounts.rbac_service import RoleAdminService, UserAdminService  # noqa: E402
from humotech.core.errors import NotFound, PermissionDenied, ValidationFailed  # noqa: E402

WIDE = ("users.manage", "roles.manage", "employees.read", "employees.manage",
        "audit.read", "offices.manage")


@pytest.fixture()
def tech_admin(organization):
    """Только управление учётками — как у TECH_ADMIN."""
    return create_actor(organization, permissions=("users.manage",),
                        raw_password="Техадмин-Вымышленный-1")


@pytest.fixture()
def super_admin(organization):
    return create_actor(organization, permissions=WIDE,
                        raw_password="Супер-Вымышленный-2026")


def test_tech_admin_cannot_take_over_a_wider_account(
    organization, tech_admin, super_admin
):
    """До исправления: TECH_ADMIN с `users.manage` ставил пароль
    суперадмину и входил под ним — 200 на set-password и 200 на вход."""
    _, actor = tech_admin
    target, _ = super_admin
    service = UserAdminService()
    with pytest.raises(PermissionDenied):
        service.set_password(actor, target.id, password="Захват-Вымышленный-99")
    with pytest.raises(PermissionDenied):
        service.update(actor, target.id, email="attacker-login")
    with pytest.raises(PermissionDenied):
        service.set_status(actor, target.id, status="INACTIVE")

    target.refresh_from_db()
    assert target.check_password("Супер-Вымышленный-2026")


def test_takeover_over_http_is_refused(organization, tech_admin, super_admin):
    tech, _ = tech_admin
    target, _ = super_admin
    client = _logged_in(tech, organization, "Техадмин-Вымышленный-1")
    response = client.post(f"{API}/users/{target.id}/set-password/",
                           {"password": "Захват-Вымышленный-99"}, format="json")
    assert response.status_code == 403
    assert _login(APIClient(), organization, target.email, "Захват-Вымышленный-99",
                  ip="198.51.100.99").status_code == 401


def test_admin_can_still_manage_narrower_accounts(organization, super_admin, tech_admin):
    _, actor = super_admin
    target, _ = tech_admin
    UserAdminService().set_password(actor, target.id, password="Новый-Вымышленный-55")
    UserAdminService().set_status(actor, target.id, status="INACTIVE")


def test_office_admin_cannot_manage_org_wide_account(organization, office, tech_admin):
    """Права те же, но территория шире — отказ."""
    target, _ = tech_admin  # назначение на всю организацию
    _, office_admin = create_actor(organization, permissions=("users.manage",),
                                   office=office)
    with pytest.raises(PermissionDenied):
        UserAdminService().set_password(office_admin, target.id,
                                        password="Захват-Вымышленный-99")


def test_future_grant_counts_as_wider(organization, tech_admin):
    _, actor = tech_admin
    target, _ = create_actor(
        organization, permissions=WIDE,
        valid_from=timezone.now() + timedelta(days=1),
    )
    with pytest.raises(PermissionDenied):
        UserAdminService().set_password(actor, target.id,
                                        password="Захват-Вымышленный-99")


def test_create_refuses_foreign_employee(organization, super_admin, other_organization):
    """До исправления `create` брал любой employee_id — и чужой организации."""
    import datetime as _dt

    from humotech.employees.models import Employee

    _, actor = super_admin
    foreign = Employee.objects.create(
        organization=other_organization,
        employee_number=f"X-{uuid.uuid4().hex[:6]}",
        first_name="Чужой", last_name="Сотрудник",
        hire_date=_dt.date(2025, 1, 1), employment_status="ACTIVE",
    )
    with pytest.raises(NotFound):
        UserAdminService().create(actor, email=f"new-{uuid.uuid4().hex[:6]}",
                                  employee_id=foreign.id)
    assert not User.objects.filter(employee_id=foreign.id).exists()


def test_create_with_weak_password_is_a_validation_error(organization, super_admin):
    _, actor = super_admin
    with pytest.raises(ValidationFailed):
        UserAdminService().create(actor, email=f"weak-{uuid.uuid4().hex[:6]}",
                                  password="123")


def test_revoke_and_extend_need_the_same_rights_as_grant(organization, office, super_admin):
    target, _ = super_admin
    grant = UserRoleScope.objects.get(user=target)
    _, office_roles_admin = create_actor(organization,
                                         permissions=("roles.manage",), office=office)
    service = RoleAdminService()
    with pytest.raises(PermissionDenied):
        service.revoke(office_roles_admin, grant.id)
    with pytest.raises(PermissionDenied):
        service.set_validity(office_roles_admin, grant.id,
                             valid_to=timezone.now() + timedelta(days=1))
    grant.refresh_from_db()
    assert grant.valid_to is None


def test_office_admin_cannot_grant_the_whole_region(organization, region, office):
    """Офис внутри региона — не повод выдавать роль на весь регион."""
    _, office_admin = create_actor(organization,
                                   permissions=("roles.manage", "employees.read"),
                                   office=office)
    target, _ = create_actor(organization)
    from humotech.rbac.models import Role
    role = Role.objects.create(organization=organization, code=f"R_{uuid.uuid4().hex[:6]}",
                               name="Узкая")
    with pytest.raises(PermissionDenied):
        RoleAdminService().assign(office_admin, user_id=target.id, role_id=role.id,
                                  region_id=region.id)
