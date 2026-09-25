"""Аудит безопасности входа в Mini App: атаки на initData и на токен.

Каждый тест — попытка злоумышленника. Ожидание везде одно: отказ
понятным кодом (400/401/403), а не 500 и не чужие данные.

Данные вымышленные, токен бота — фиктивный из `conftest.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode

import pytest
from django.core import signing
from rest_framework.test import APIClient

from django_tests.conftest import (
    HR_FULL_PERMISSIONS,
    TEST_BOT_SECRET,
    TEST_BOT_TOKEN,
    build_init_data,
    link_telegram,
)
from humotech.telegram.initdata import InitDataError, verify_init_data
from humotech.telegram.models import TelegramAccount
from humotech.telegram.tokens import (
    MINI_APP_SALT,
    MiniAppClaims,
    issue_mini_app_token,
    read_mini_app_token,
)

pytestmark = pytest.mark.django_db

API = "/api/v1"
AUTH = f"{API}/telegram/mini-app/auth"
TG_ID = 881_001


def _sign(pairs: list[tuple[str, str]], *, bot_token: str = TEST_BOT_TOKEN) -> str:
    """Подписать произвольный набор пар ровно так, как это делает Telegram.

    Нужен там, где `build_init_data` не умеет: дубли полей, странные даты.
    Подпись при этом НАСТОЯЩАЯ — проверяется реакция сервера на
    подписанную, но необычную строку.
    """
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs, key=lambda p: p[0]))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode([*pairs, ("hash", digest)])


def _user(uid: int = TG_ID) -> str:
    return json.dumps({"id": uid, "first_name": "Тест"}, separators=(",", ":"))


def _verify(init_data: str):
    return verify_init_data(init_data, bot_token=TEST_BOT_TOKEN, max_age_seconds=300)


def _login(client: APIClient, uid: int = TG_ID) -> str:
    response = client.post(AUTH, {"init_data": build_init_data(telegram_user_id=uid)})
    assert response.status_code == 200, response.content
    return response.json()["access_token"]


@pytest.fixture()
def linked(employee, telegram_settings):
    return link_telegram(employee, telegram_user_id=TG_ID)


@pytest.fixture()
def client():
    return APIClient()


# --- initData: подпись и состав ---------------------------------------------


def test_foreign_user_id_substituted_after_signing_is_rejected(linked, client):
    """Подмена user.id в чужой подписанной строке."""
    forged = build_init_data(
        telegram_user_id=999_999,
        tamper={"user": _user(TG_ID)},
    )
    response = client.post(AUTH, {"init_data": forged})
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "init_data_rejected"


def test_string_signed_by_another_bot_is_rejected(linked, client):
    other = build_init_data(telegram_user_id=TG_ID, bot_token="42:ANOTHER-FAKE-BOT")
    assert client.post(AUTH, {"init_data": other}).status_code == 403


@pytest.mark.parametrize("hash_value", ["", "0" * 64, "A" * 64, "ы" * 64])
def test_empty_or_guessed_hash_is_rejected(linked, client, hash_value):
    forged = build_init_data(telegram_user_id=TG_ID, tamper={"hash": hash_value})
    assert client.post(AUTH, {"init_data": forged}).status_code == 403


def test_second_hash_is_rejected():
    good = build_init_data()
    with pytest.raises(InitDataError) as exc:
        _verify(good + "&hash=" + "0" * 64)
    assert exc.value.reason == "duplicate_hash"


def test_duplicate_signed_field_is_rejected():
    """Два `user` в одной подписанной строке.

    Подписать так Telegram не может, но строка всё равно не должна
    приниматься: какой из двух `user` считать владельцем — неоднозначно.
    """
    now = str(int(time.time()))
    doubled = _sign([("auth_date", now), ("user", _user(1)), ("user", _user(TG_ID))])
    with pytest.raises(InitDataError) as exc:
        _verify(doubled)
    assert exc.value.reason == "duplicate_field"


def test_field_order_does_not_matter():
    now = str(int(time.time()))
    a = _sign([("auth_date", now), ("user", _user())])
    b = _sign([("user", _user()), ("auth_date", now)])
    assert _verify(a).user.id == _verify(b).user.id == TG_ID


# --- initData: срок ----------------------------------------------------------


def test_expired_string_is_refused_over_http(linked, client):
    old = datetime.now(tz=dt_timezone.utc) - timedelta(minutes=6)
    data = build_init_data(telegram_user_id=TG_ID, auth_date=old)
    assert client.post(AUTH, {"init_data": data}).status_code == 403


def test_future_string_is_refused_over_http(linked, client):
    ahead = datetime.now(tz=dt_timezone.utc) + timedelta(minutes=10)
    data = build_init_data(telegram_user_id=TG_ID, auth_date=ahead)
    assert client.post(AUTH, {"init_data": data}).status_code == 403


@pytest.mark.parametrize(
    "auth_date", ["9" * 30, "-" + "9" * 30, "99999999999999", "1e10", "٣٤٥"]
)
def test_absurd_signed_auth_date_is_refused_not_crashed(auth_date):
    """Подписанная строка с датой вне диапазона `datetime`.

    До исправления `datetime.fromtimestamp` бросал OverflowError/OSError,
    которые не ловились, и запрос заканчивался 500.
    """
    data = _sign([("auth_date", auth_date), ("user", _user())])
    with pytest.raises(InitDataError):
        _verify(data)


def test_absurd_signed_auth_date_over_http_is_403(linked, client):
    data = _sign([("auth_date", "9" * 30), ("user", _user())])
    assert client.post(AUTH, {"init_data": data}).status_code == 403


@pytest.mark.parametrize(
    "user",
    [
        '{"id":true}',
        '{"id":1.5}',
        '{"id":"12abc"}',
        '{"id":[1]}',
        '{"id":-5}',
        "[]",
        '"x"',
        '{"id":' + "9" * 5000 + "}",
    ],
)
def test_strange_signed_user_is_refused_not_crashed(user):
    data = _sign([("auth_date", str(int(time.time()))), ("user", user)])
    with pytest.raises(InitDataError):
        _verify(data)


def test_replay_inside_the_window_gives_a_fresh_token(linked, client):
    """Повтор той же строки в окне пяти минут — принимается.

    Это осознанное поведение: перезагрузка вебвью присылает ту же строку.
    Тест фиксирует его, чтобы изменение было замечено.
    """
    data = build_init_data(telegram_user_id=TG_ID)
    assert client.post(AUTH, {"init_data": data}).status_code == 200
    assert client.post(AUTH, {"init_data": data}).status_code == 200


# --- initData: кривой ввод ---------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"init_data": None},
        {"init_data": ""},
        {"init_data": ["a=1"]},
        {"init_data": {"hash": "x"}},
        {"init_data": 12345},
        {"init_data": "&&&==="},
        {"init_data": "a\x00b=1&hash=x"},
        {"init_data": "%ED%A0%80=1&hash=%ED%A0%80"},
        {"init_data": "hash=" + "ы" * 3000},
        {"init_data": {"a": {"b": {"c": {"d": [[[[1]]]]}}}}},
    ],
)
def test_garbage_init_data_is_refused_not_crashed(linked, client, payload):
    response = client.post(AUTH, payload, format="json")
    assert response.status_code in (400, 403), response.content


@pytest.mark.parametrize(
    "raw",
    [
        '{"init_data": "\\ud800=1&hash=x"}',
        '{"init_data": "user=1&hash=\\udfff"}',
        '{"init_data": "a=1&hash=x", "init_data": "b=2&hash=y"}',
        '{"init_data": "' + "[" * 5000 + '"}',
        # Было 500 (RecursionError в JSONParser DRF); закрыто зоной generic
        # в core/exceptions.py — здесь держится как регрессия.
        "[" * 10000,
        '{"init_data": 1e999}',
    ],
)
def test_raw_json_body_is_refused_not_crashed(linked, client, raw):
    """Тело, которое тестовый клиент сам не соберёт: одиночные суррогаты,
    повторный ключ, глубокая вложенность."""
    response = client.generic("POST", AUTH, raw, content_type="application/json")
    assert response.status_code in (400, 403), response.content


def test_oversized_init_data_is_refused_cheaply(linked, client):
    huge = "a=" + "x" * 500_000 + "&hash=" + "0" * 64
    started = time.monotonic()
    response = client.post(AUTH, {"init_data": huge}, format="json")
    assert response.status_code in (400, 403)
    assert time.monotonic() - started < 2


def test_many_fields_are_refused():
    many = "&".join(f"k{i}=v" for i in range(5000)) + "&hash=" + "0" * 64
    with pytest.raises(InitDataError):
        _verify(many)


def test_lone_surrogate_does_not_crash_the_verifier():
    """Строка с одиночным суррогатом приходит не только через DRF.

    Сам верификатор обязан отказать, а не уронить запрос
    UnicodeEncodeError при подсчёте HMAC.
    """
    with pytest.raises(InitDataError):
        _verify("user=\ud800&auth_date=1&hash=" + "0" * 64)


# --- внутренний токен --------------------------------------------------------


def _claims(account: TelegramAccount) -> MiniAppClaims:
    return MiniAppClaims(
        telegram_account_id=str(account.id),
        telegram_user_id=account.telegram_user_id,
        employee_id=str(account.employee_id),
        organization_id=str(account.organization_id),
    )


def test_token_signed_without_the_salt_is_rejected(linked):
    """Строка, подписанная тем же ключом для другой цели, не годится."""
    payload = {
        "telegram_account_id": str(linked.id),
        "telegram_user_id": linked.telegram_user_id,
        "employee_id": str(linked.employee_id),
        "organization_id": str(linked.organization_id),
    }
    assert read_mini_app_token(signing.dumps(payload), max_age_seconds=60) is None
    assert (
        read_mini_app_token(signing.dumps(payload, salt="other"), max_age_seconds=60)
        is None
    )


def test_token_signed_with_another_key_is_rejected(linked, settings):
    settings.SECRET_KEY = "attacker-guess-not-real"
    forged = issue_mini_app_token(_claims(linked))
    settings.SECRET_KEY = "test-only-not-a-real-secret-key-2"
    assert read_mini_app_token(forged, max_age_seconds=60) is None


def test_tampered_token_is_rejected(linked, client):
    token = _login(client)
    body, _, sig = token.rpartition(":")
    flipped = body[:-1] + ("A" if body[-1] != "A" else "B") + ":" + sig
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {flipped}")
    assert client.get(f"{API}/me/profile").status_code == 401


def test_token_cannot_be_extended_by_rewriting_the_timestamp(linked, client):
    token = _login(client)
    parts = token.split(":")
    # Формат django.core.signing: <payload>:<timestamp>:<signature>.
    parts[-2] = signing.b62_encode(int(time.time()) + 10 * 86400)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {':'.join(parts)}")
    assert client.get(f"{API}/me/profile").status_code == 401


def test_token_expires_after_twelve_hours(linked, client, monkeypatch):
    real = time.time
    monkeypatch.setattr(signing.time, "time", lambda: real() - 12 * 3600 - 5)
    old = issue_mini_app_token(_claims(linked))
    monkeypatch.setattr(signing.time, "time", real)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {old}")
    assert client.get(f"{API}/me/profile").status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "Bearer",
        "Bearer ",
        "Bearer a b",
        "Bearer " + "x" * 200_000,
        "Bearer \xff\xfe",
        "Bearer ::::",
        "Bearer e30:1:abc",
        "Basic dXNlcjpwYXNz",
    ],
)
def test_malformed_bearer_is_refused_not_crashed(linked, client, header):
    client.credentials(HTTP_AUTHORIZATION=header)
    response = client.get(f"{API}/me/profile")
    assert response.status_code in (401, 403), response.status_code


def test_token_dies_when_the_binding_is_revoked(linked, client):
    token = _login(client)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.get(f"{API}/me/profile").status_code == 200

    linked.status = "REVOKED"
    linked.revoked_at = datetime.now(tz=dt_timezone.utc)
    linked.save()
    assert client.get(f"{API}/me/profile").status_code == 401


@pytest.mark.parametrize("status", ["TERMINATED", "ARCHIVED"])
def test_token_dies_when_the_employee_is_deactivated(linked, client, employee, status):
    token = _login(client)
    employee.employment_status = status
    employee.save()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.get(f"{API}/me/profile").status_code == 401


def test_token_dies_when_the_binding_moves_to_another_telegram(linked, client):
    token = _login(client)
    linked.telegram_user_id = TG_ID + 1
    linked.telegram_chat_id = TG_ID + 1
    linked.save()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.get(f"{API}/me/profile").status_code == 401


@pytest.fixture()
def hr_client(make_user, organization, telegram_settings):
    hr = APIClient()
    hr.force_authenticate(
        user=make_user(
            organization,
            permissions=HR_FULL_PERMISSIONS + ("telegram.read", "telegram.manage"),
        )
    )
    return hr


def _link_via_api(hr_client, employee, telegram_user_id: int) -> None:
    """Полный путь привязки: ссылка HR -> переход в боте -> подтверждение HR."""
    bot = APIClient()
    invite = hr_client.post(
        f"{API}/telegram/invitations/", {"employee_id": str(employee.id)}
    ).json()
    consumed = bot.post(
        f"{API}/telegram/bot/link",
        {
            "token": invite["token"],
            "telegram_user_id": telegram_user_id,
            "telegram_chat_id": telegram_user_id,
        },
        HTTP_X_BOT_TOKEN=TEST_BOT_SECRET,
    )
    assert consumed.status_code in (200, 201), consumed.content
    confirmed = hr_client.post(
        f"{API}/telegram/invitations/{invite['invitation']['id']}/confirm/"
    )
    assert confirmed.status_code == 200, confirmed.content


def test_token_issued_before_revocation_stays_dead_after_relink(
    hr_client, client, employee, monkeypatch
):
    """Отзыв и повторная привязка того же Telegram.

    Сценарий: телефон украли, HR отключил привязку; потом сотрудник
    вернул себе аккаунт, и HR привязал его заново. Токен, снятый с
    украденного телефона ДО отзыва, не должен ожить.

    До исправления: 200 — строка привязки та же, `telegram_user_id` тот же,
    и сверять было нечего.
    """
    _link_via_api(hr_client, employee, TG_ID)
    account = TelegramAccount.objects.get(employee=employee)
    real = time.time
    # Токен выпущен минутой раньше повторной привязки.
    monkeypatch.setattr(signing.time, "time", lambda: real() - 60)
    stolen = issue_mini_app_token(_claims(account))
    monkeypatch.setattr(signing.time, "time", real)

    assert hr_client.post(
        f"{API}/employees/{employee.id}/telegram/disconnect"
    ).status_code == 200
    _link_via_api(hr_client, employee, TG_ID)
    assert TelegramAccount.objects.get(employee=employee).status == "ACTIVE"

    client.credentials(HTTP_AUTHORIZATION=f"Bearer {stolen}")
    assert client.get(f"{API}/me/profile").status_code == 401

    # А свежий вход после повторной привязки работает.
    fresh = APIClient()
    fresh.credentials(HTTP_AUTHORIZATION=f"Bearer {_login(fresh)}")
    assert fresh.get(f"{API}/me/profile").status_code == 200


# --- чужие территории --------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", f"{API}/employees/"),
        ("get", f"{API}/offices/"),
        ("get", f"{API}/auth/me"),
        ("get", f"{API}/telegram/invitations/"),
        ("get", f"{API}/telegram/bot/outbox"),
        ("post", f"{API}/telegram/bot/link"),
    ],
)
def test_employee_token_does_not_open_hr_or_bot_api(linked, client, method, path):
    token = _login(client)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = getattr(client, method)(path, {}, format="json")
    assert response.status_code in (401, 403), (path, response.status_code)


def test_employee_token_does_not_open_django_admin(linked, client):
    token = _login(client)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = client.get("/admin/")
    # Admin отправляет на свою форму входа: токен для него не существует.
    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_employee_token_does_not_pass_as_bot_secret(linked, client):
    token = _login(client)
    response = client.get(f"{API}/telegram/bot/outbox", HTTP_X_BOT_TOKEN=token)
    assert response.status_code in (401, 403)


def test_hr_cookie_does_not_open_employee_cabinet(linked, make_user, organization):
    """Сессия HR — не сотрудник: `/me/*` её не принимает."""
    hr = APIClient()
    hr.force_login(make_user(organization, permissions=HR_FULL_PERMISSIONS))
    assert hr.get(f"{API}/employees/").status_code == 200  # сессия рабочая
    for path in ("profile", "status", "absences", "surveys", "notifications"):
        response = hr.get(f"{API}/me/{path}")
        assert response.status_code in (401, 403), (path, response.status_code)
    assert hr.get(f"{API}/telegram/mini-app/me").status_code in (401, 403)


def test_bot_telegram_id_header_without_secret_is_refused(linked, client):
    response = client.get(
        f"{API}/me/profile", HTTP_X_TELEGRAM_USER_ID=str(TG_ID)
    )
    assert response.status_code == 401


def test_bot_telegram_id_header_does_not_override_bearer(
    linked, client, employee, organization, office
):
    """Bearer одного сотрудника + заголовок Telegram ID другого.

    Побеждает Bearer, и в ответе ровно владелец токена.
    """
    from humotech.employees.models import Employee, EmployeeAssignment

    other = Employee.objects.create(
        organization=organization,
        employee_number="EMP-SEC-2",
        first_name="Пётр",
        last_name="Тестов",
        hire_date=datetime(2024, 1, 1).date(),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=other, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=datetime(2024, 1, 1).date(),
    )
    link_telegram(other, telegram_user_id=TG_ID + 50, username="petr")

    token = _login(client)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = client.get(
        f"{API}/me/profile",
        HTTP_X_TELEGRAM_USER_ID=str(TG_ID + 50),
        HTTP_X_BOT_TOKEN=TEST_BOT_SECRET + "-wrong",
    )
    assert response.status_code == 200
    assert response.json()["employee"]["id"] == str(employee.id)


def test_client_supplied_ids_are_ignored_on_auth(linked, client, other_organization):
    response = client.post(
        AUTH,
        {
            "init_data": build_init_data(telegram_user_id=TG_ID),
            "employee_id": "00000000-0000-0000-0000-000000000001",
            "organization_id": str(other_organization.id),
            "telegram_user_id": 1,
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["employee"]["id"] == str(linked.employee_id)
