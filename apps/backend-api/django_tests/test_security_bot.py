"""Безопасность входа бота: общий секрет, привязка, отзыв, кривой ввод.

Атакующий проход по зоне bot. Каждый тест формулирует, что ДОЛЖНО быть;
тесты, которые падали до исправления, помечены в докстринге «(было: …)».

Все данные вымышленные, секрет — тестовый (`TEST_BOT_SECRET`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone
import uuid

import pytest

from django_tests.conftest import (
    HR_FULL_PERMISSIONS,
    TEST_BOT_SECRET,
    bot_headers,
    link_telegram,
)
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation

pytestmark = pytest.mark.django_db

API = "/api/v1"
TELEGRAM_FULL = HR_FULL_PERMISSIONS + ("telegram.read", "telegram.manage")
SECRET = {"HTTP_X_BOT_TOKEN": TEST_BOT_SECRET}


@pytest.fixture()
def hr_client(make_user, organization, telegram_settings):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=make_user(organization, permissions=TELEGRAM_FULL))
    return client


@pytest.fixture()
def other_employee(db, organization, office) -> Employee:
    emp = Employee.objects.create(
        organization=organization,
        employee_number="EMP-0777",
        first_name="Пётр",
        last_name="Вымышленный",
        hire_date=date(2024, 2, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=emp, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 2, 1),
    )
    return emp


def _invite(hr_client, employee) -> dict:
    response = hr_client.post(
        f"{API}/telegram/invitations/", {"employee_id": str(employee.id)},
        format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()


def _consume(client, token, telegram_user_id=880_001, **extra):
    body = {
        "token": token,
        "telegram_user_id": telegram_user_id,
        "telegram_chat_id": telegram_user_id,
    }
    body.update(extra)
    return client.post(f"{API}/telegram/bot/link", body, format="json", **SECRET)


# --- 1. Общий секрет ---------------------------------------------------------


class TestBotSecret:
    """X-Bot-Token: отсутствует, пустой, неверный, не-ASCII, не настроен."""

    @pytest.mark.parametrize(
        "secret",
        [None, "", "wrong", TEST_BOT_SECRET + "x", TEST_BOT_SECRET[:-1],
         " " + TEST_BOT_SECRET, "секрет-кириллицей", "\x00" * 8],
    )
    def test_self_service_refuses_bad_secret(
        self, bot_client, linked_account, secret
    ):
        headers = {"HTTP_X_TELEGRAM_USER_ID": str(linked_account.telegram_user_id)}
        if secret is not None:
            headers["HTTP_X_BOT_TOKEN"] = secret
        response = bot_client.get(f"{API}/me/profile", **headers)
        assert response.status_code in (401, 403)
        assert "employee" not in response.json()

    @pytest.mark.parametrize("secret", [None, "", "wrong", "секрет"])
    def test_bot_only_endpoints_refuse_bad_secret(
        self, bot_client, telegram_settings, secret
    ):
        headers = {} if secret is None else {"HTTP_X_BOT_TOKEN": secret}
        for method, path, body in (
            ("post", "/telegram/bot/link",
             {"token": "x", "telegram_user_id": 1, "telegram_chat_id": 1}),
            ("post", "/telegram/bot/link/accept", {"telegram_user_id": 1}),
            ("post", "/telegram/bot/recognize",
             {"telegram_user_id": 1, "telegram_chat_id": 1,
              "telegram_username": "x"}),
            ("get", "/telegram/bot/outbox", None),
            ("post", "/telegram/bot/outbox", {"results": []}),
        ):
            call = getattr(bot_client, method)
            response = (
                call(f"{API}{path}", body, format="json", **headers)
                if body is not None else call(f"{API}{path}", **headers)
            )
            assert response.status_code in (401, 403), (path, response.status_code)

    @pytest.mark.parametrize("configured", ["", None])
    def test_unset_server_secret_closes_everything(
        self, bot_client, settings, linked_account, configured
    ):
        """Секрет на сервере не задан — вход закрыт даже с пустым заголовком."""
        settings.TELEGRAM = {**settings.TELEGRAM, "BOT_API_SECRET": configured}
        for secret in ("", "None", "anything"):
            profile = bot_client.get(
                f"{API}/me/profile",
                HTTP_X_BOT_TOKEN=secret,
                HTTP_X_TELEGRAM_USER_ID=str(linked_account.telegram_user_id),
            )
            assert profile.status_code in (401, 403)
            link = bot_client.post(
                f"{API}/telegram/bot/link/accept", {"telegram_user_id": 1},
                format="json", HTTP_X_BOT_TOKEN=secret,
            )
            assert link.status_code in (401, 403)

    def test_secret_without_telegram_id_is_not_an_employee(
        self, bot_client, linked_account
    ):
        response = bot_client.get(f"{API}/me/profile", **SECRET)
        assert response.status_code in (401, 403)

    def test_comparison_is_constant_time_by_construction(self):
        """Сравниваются хеши одинаковой длины через compare_digest."""
        from humotech.telegram import compare

        assert compare.constant_time_equal("abc", "abc")
        assert not compare.constant_time_equal("", "abc")
        assert not compare.constant_time_equal("абв", "abc")  # без TypeError
        assert len(compare._digest("x")) == len(compare._digest("y" * 10_000))


# --- 2. Модель доверия: что можно с секретом бота ---------------------------


class TestBotTrustModel:
    """Секрет + любой Telegram ID = действовать за этого сотрудника.

    Это принятая модель (секрет есть только у бота). Проверяем, что за её
    пределы она не выходит: HR-операции через bot-auth недоступны.
    """

    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/employees/"),
            ("get", "/telegram/invitations/"),
            ("post", "/telegram/invitations/"),
            ("get", "/absences/"),
            ("get", "/questions/"),
            ("get", "/notifications/"),
            ("get", "/surveys/"),
        ],
    )
    def test_bot_headers_do_not_open_hr_api(
        self, bot_client, linked_account, method, path
    ):
        response = getattr(bot_client, method)(
            f"{API}{path}", **bot_headers(linked_account.telegram_user_id)
        )
        assert response.status_code in (401, 403, 404, 405), (path, response.status_code)

    def test_hr_employee_card_is_closed_to_bot(
        self, bot_client, linked_account, employee
    ):
        headers = bot_headers(linked_account.telegram_user_id)
        assert bot_client.get(
            f"{API}/employees/{employee.id}/telegram", **headers
        ).status_code in (401, 403)
        assert bot_client.post(
            f"{API}/employees/{employee.id}/telegram/disconnect", **headers
        ).status_code in (401, 403)
        assert TelegramAccount.objects.get(pk=linked_account.pk).status == "ACTIVE"

    def test_bot_cannot_confirm_pending_link(
        self, bot_client, hr_client, employee
    ):
        invitation = _invite(hr_client, employee)
        _consume(bot_client, invitation["token"])
        response = bot_client.post(
            f"{API}/telegram/invitations/{invitation['invitation']['id']}/confirm/",
            **bot_headers(880_001),
        )
        assert response.status_code in (401, 403)

    def test_body_employee_id_is_ignored(
        self, bot_client, linked_account, other_employee
    ):
        """employee_id/organization_id в запросе не читаются."""
        response = bot_client.get(
            f"{API}/me/profile",
            {"employee_id": str(other_employee.id)},
            **bot_headers(linked_account.telegram_user_id),
        )
        assert response.status_code == 200
        assert response.json()["employee"]["id"] == str(linked_account.employee_id)

    @pytest.mark.parametrize(
        "raw",
        ["", "abc", "1e3", "-1", "0", "1.5", "0x10",
         "99999999999999999999999", "9" * 5000, "７７７", "777 000"],
    )
    def test_weird_telegram_id_is_refused_not_500(
        self, bot_client, linked_account, raw
    ):
        response = bot_client.get(
            f"{API}/me/profile",
            HTTP_X_BOT_TOKEN=TEST_BOT_SECRET,
            HTTP_X_TELEGRAM_USER_ID=raw,
        )
        assert response.status_code in (401, 403)

    @pytest.mark.parametrize(
        "spelled", ["777_000_111", " 777000111", "777000111 ", "+777000111",
                    "７７７０００１１１"],
    )
    def test_alternative_spellings_of_linked_id_are_refused(
        self, bot_client, linked_account, spelled
    ):
        """(было: `int()` принимал подчёркивания, пробелы, «+» и цифры
        других алфавитов — одна привязка отзывалась на много написаний.)"""
        assert linked_account.telegram_user_id == 777_000_111
        response = bot_client.get(
            f"{API}/me/profile",
            HTTP_X_BOT_TOKEN=TEST_BOT_SECRET,
            HTTP_X_TELEGRAM_USER_ID=spelled,
        )
        assert response.status_code in (401, 403)


# --- 3. Отзыв доступа ---------------------------------------------------------


class TestRevocation:
    def _me(self, client, account):
        return client.get(f"{API}/me/profile", **bot_headers(account.telegram_user_id))

    def test_hr_disconnect_blocks_next_request(
        self, bot_client, hr_client, linked_account, employee
    ):
        assert self._me(bot_client, linked_account).status_code == 200
        hr_client.post(f"{API}/employees/{employee.id}/telegram/disconnect")
        refused = self._me(bot_client, linked_account)
        assert refused.status_code in (401, 403)

    @pytest.mark.parametrize("status", ["TERMINATED", "ARCHIVED"])
    def test_inactive_employee_is_blocked(
        self, bot_client, linked_account, employee, status
    ):
        Employee.objects.filter(pk=employee.pk).update(employment_status=status)
        assert self._me(bot_client, linked_account).status_code in (401, 403)

    def test_archived_employee_is_blocked(self, bot_client, linked_account, employee):
        Employee.objects.filter(pk=employee.pk).update(
            archived_at=datetime.now(tz=dt_timezone.utc)
        )
        assert self._me(bot_client, linked_account).status_code in (401, 403)

    def test_frozen_organization_is_blocked(
        self, bot_client, linked_account, organization
    ):
        type(organization).objects.filter(pk=organization.pk).update(status="INACTIVE")
        assert self._me(bot_client, linked_account).status_code in (401, 403)

    def test_assignment_ended_is_blocked(self, bot_client, linked_account, employee):
        EmployeeAssignment.objects.filter(employee=employee).update(
            valid_to=date(2024, 3, 1)
        )
        assert self._me(bot_client, linked_account).status_code in (401, 403)

    @pytest.mark.parametrize("status", ["PENDING", "REVOKED", "BLOCKED"])
    def test_not_active_link_is_blocked(self, bot_client, employee, telegram_settings, status):
        account = link_telegram(employee, telegram_user_id=880_555, status=status)
        assert self._me(bot_client, account).status_code in (401, 403)

    def test_disconnect_revokes_open_invitation_too(
        self, bot_client, hr_client, employee
    ):
        """Отключение закрывает и живую ссылку: по ней снова в PENDING не попасть."""
        invitation = _invite(hr_client, employee)
        _consume(bot_client, invitation["token"])
        hr_client.post(f"{API}/employees/{employee.id}/telegram/disconnect")
        again = _consume(bot_client, invitation["token"])
        assert again.status_code == 409
        assert again.json()["error"]["details"]["reason"] in ("revoked", "used", "pending")
        assert TelegramAccount.objects.get(employee=employee).status == "REVOKED"


# --- 4. Ссылки привязки -------------------------------------------------------


class TestLinkTokens:
    def test_token_is_single_use(self, bot_client, hr_client, employee):
        invitation = _invite(hr_client, employee)
        assert _consume(bot_client, invitation["token"]).status_code == 201
        second = _consume(bot_client, invitation["token"], telegram_user_id=880_002)
        assert second.status_code == 409
        # Второй Telegram не перехватил привязку.
        assert TelegramAccount.objects.get(employee=employee).telegram_user_id == 880_001

    def test_used_token_after_accept(self, bot_client, hr_client, employee):
        invitation = _invite(hr_client, employee)
        _consume(bot_client, invitation["token"])
        bot_client.post(
            f"{API}/telegram/bot/link/accept", {"telegram_user_id": 880_001},
            format="json", **SECRET,
        )
        again = _consume(bot_client, invitation["token"], telegram_user_id=880_003)
        assert again.status_code == 409
        assert again.json()["error"]["details"]["reason"] == "used"
        assert TelegramAccount.objects.get(employee=employee).telegram_user_id == 880_001

    def test_expired_token(self, bot_client, hr_client, employee):
        invitation = _invite(hr_client, employee)
        TelegramLinkInvitation.objects.update(
            expires_at=datetime.now(tz=dt_timezone.utc) - timedelta(seconds=1)
        )
        response = _consume(bot_client, invitation["token"])
        assert response.status_code == 409
        assert response.json()["error"]["details"]["reason"] == "expired"
        assert not TelegramAccount.objects.exists()

    def test_revoked_token(self, bot_client, hr_client, employee):
        invitation = _invite(hr_client, employee)
        hr_client.post(
            f"{API}/telegram/invitations/{invitation['invitation']['id']}/revoke/"
        )
        response = _consume(bot_client, invitation["token"])
        assert response.json()["error"]["details"]["reason"] == "revoked"

    @pytest.mark.parametrize(
        "token",
        ["", " ", "x" * 129, "' OR 1=1 --", "токен", "\x00", "%00", "a" * 43],
    )
    def test_garbage_tokens(self, bot_client, telegram_settings, token):
        response = _consume(bot_client, token)
        assert response.status_code in (400, 409)
        assert not TelegramAccount.objects.exists()

    def test_telegram_of_other_employee_cannot_take_second_link(
        self, bot_client, hr_client, employee, other_employee
    ):
        link_telegram(other_employee, telegram_user_id=880_900)
        invitation = _invite(hr_client, employee)
        response = _consume(bot_client, invitation["token"], telegram_user_id=880_900)
        assert response.status_code == 409
        assert response.json()["error"]["details"]["reason"] == "telegram_taken"

    def test_no_second_invitation_while_active_link(
        self, hr_client, employee, telegram_settings
    ):
        link_telegram(employee, telegram_user_id=880_901)
        response = hr_client.post(
            f"{API}/telegram/invitations/",
            {"employee_id": str(employee.id), "replace": True}, format="json",
        )
        assert response.status_code == 409

    def test_accept_only_by_the_consumer(self, bot_client, hr_client, employee):
        invitation = _invite(hr_client, employee)
        _consume(bot_client, invitation["token"], telegram_user_id=880_010)
        foreign = bot_client.post(
            f"{API}/telegram/bot/link/accept", {"telegram_user_id": 880_011},
            format="json", **SECRET,
        )
        assert foreign.status_code == 409
        assert TelegramAccount.objects.get(employee=employee).status == "PENDING"

    def test_accept_twice_is_conflict(self, bot_client, hr_client, employee):
        invitation = _invite(hr_client, employee)
        _consume(bot_client, invitation["token"], telegram_user_id=880_012)
        body = {"telegram_user_id": 880_012}
        first = bot_client.post(f"{API}/telegram/bot/link/accept", body, format="json", **SECRET)
        again = bot_client.post(f"{API}/telegram/bot/link/accept", body, format="json", **SECRET)
        assert first.status_code == 200
        assert again.status_code == 409

    def test_accept_refused_for_rejected_link(self, bot_client, hr_client, employee):
        invitation = _invite(hr_client, employee)
        _consume(bot_client, invitation["token"], telegram_user_id=880_013)
        hr_client.post(
            f"{API}/telegram/invitations/{invitation['invitation']['id']}/reject/"
        )
        response = bot_client.post(
            f"{API}/telegram/bot/link/accept", {"telegram_user_id": 880_013},
            format="json", **SECRET,
        )
        assert response.status_code == 409
        assert TelegramAccount.objects.get(employee=employee).status == "REVOKED"


class TestRecognizeNeedsHr:
    """Узнавание по @username не должно само открывать доступ.

    (было: recognize -> PENDING, затем /bot/link/accept -> ACTIVE без HR.
    Кто занял в Telegram имя, которое кадровик вписал в карточку, получал
    доступ к кабинету чужого сотрудника одним нажатием.)
    """

    def _recognize(self, client, username="fict_new_hire", telegram_user_id=880_300):
        return client.post(
            f"{API}/telegram/bot/recognize",
            {"telegram_user_id": telegram_user_id,
             "telegram_chat_id": telegram_user_id,
             "telegram_username": username},
            format="json", **SECRET,
        )

    def _prepare(self, hr_client, employee):
        _invite(hr_client, employee)
        TelegramLinkInvitation.objects.filter(employee_id=employee.id).update(
            expected_username="fict_new_hire"
        )

    def test_accept_after_recognize_is_refused(self, bot_client, hr_client, employee):
        self._prepare(hr_client, employee)
        assert self._recognize(bot_client).status_code == 201

        response = bot_client.post(
            f"{API}/telegram/bot/link/accept", {"telegram_user_id": 880_300},
            format="json", **SECRET,
        )
        assert response.status_code == 409
        assert response.json()["error"]["details"]["reason"] == "hr_confirmation_required"
        assert TelegramAccount.objects.get(employee=employee).status == "PENDING"
        # И бот за этого человека ничего не видит.
        assert bot_client.get(
            f"{API}/me/profile", **bot_headers(880_300)
        ).status_code in (401, 403)

    def test_hr_can_still_confirm_recognized(self, bot_client, hr_client, employee):
        self._prepare(hr_client, employee)
        self._recognize(bot_client)
        invitation = TelegramLinkInvitation.objects.get(employee_id=employee.id)
        confirmed = hr_client.post(
            f"{API}/telegram/invitations/{invitation.id}/confirm/"
        )
        assert confirmed.status_code == 200, confirmed.content
        assert bot_client.get(
            f"{API}/me/profile", **bot_headers(880_300)
        ).status_code == 200

    def test_link_still_activates_by_consent(self, bot_client, hr_client, employee):
        """Персональная ссылка по-прежнему активируется согласием сотрудника."""
        invitation = _invite(hr_client, employee)
        _consume(bot_client, invitation["token"], telegram_user_id=880_301)
        response = bot_client.post(
            f"{API}/telegram/bot/link/accept", {"telegram_user_id": 880_301},
            format="json", **SECRET,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ACTIVE"

    def test_recognize_username_is_case_and_at_insensitive_but_exact(
        self, bot_client, hr_client, employee
    ):
        self._prepare(hr_client, employee)
        # Подстрока и шаблоны LIKE не узнаются.
        for guess in ("fict_new", "fict%", "fict_new_hir_", "%"):
            response = self._recognize(bot_client, username=guess)
            assert response.status_code == 409, guess
        assert not TelegramAccount.objects.exists()


# --- 5. Кривой ввод: никаких 500 ----------------------------------------------


class TestMalformedInput:
    """(было: числа вне bigint и длинные строки доходили до базы -> 500.)"""

    BIG = 10**20

    @pytest.mark.parametrize(
        "extra",
        [
            {"telegram_user_id": BIG},
            {"telegram_chat_id": BIG},
            {"telegram_chat_id": -BIG},
            {"telegram_username": "u" * 300},
            {"language_code": "x" * 11},
            {"telegram_user_id": "1; DROP TABLE telegram_accounts"},
            {"telegram_user_id": True},
            {"telegram_user_id": [1]},
            {"telegram_user_id": {"a": 1}},
        ],
    )
    def test_link_consume(self, bot_client, hr_client, employee, extra):
        invitation = _invite(hr_client, employee)
        response = _consume(bot_client, invitation["token"], **extra)
        assert response.status_code in (400, 409), response.content
        assert TelegramLinkInvitation.objects.get().status == "ACTIVE"

    @pytest.mark.parametrize(
        "body",
        [
            {"telegram_user_id": BIG, "telegram_chat_id": 1, "telegram_username": "x"},
            {"telegram_user_id": 1, "telegram_chat_id": BIG, "telegram_username": "x"},
            {"telegram_user_id": 0, "telegram_chat_id": 1, "telegram_username": "x"},
            {"telegram_user_id": -5, "telegram_chat_id": 1, "telegram_username": "x"},
            {"telegram_user_id": 1, "telegram_chat_id": 1, "telegram_username": "u" * 300},
            {"telegram_user_id": 1, "telegram_chat_id": 1, "telegram_username": "@"},
            {"telegram_user_id": 1, "telegram_chat_id": 1, "telegram_username": "‮\u0000"},
        ],
    )
    def test_recognize(self, bot_client, telegram_settings, body):
        response = bot_client.post(
            f"{API}/telegram/bot/recognize", body, format="json", **SECRET
        )
        assert response.status_code in (400, 409), response.content

    @pytest.mark.parametrize(
        "over",
        [{"telegram_user_id": BIG}, {"telegram_chat_id": BIG},
         {"telegram_user_id": 0}, {"telegram_user_id": -7}],
    )
    def test_recognize_bad_ids_with_live_invitation(
        self, bot_client, hr_client, employee, over
    ):
        """(было: при живом приглашении число вне bigint -> 500, ноль и
        отрицательный ID сохранялись в привязку.)"""
        _invite(hr_client, employee)
        TelegramLinkInvitation.objects.update(expected_username="fict_user")
        body = {"telegram_user_id": 880_400, "telegram_chat_id": 880_400,
                "telegram_username": "fict_user", **over}
        response = bot_client.post(
            f"{API}/telegram/bot/recognize", body, format="json", **SECRET
        )
        assert response.status_code == 400, response.content
        assert not TelegramAccount.objects.exists()

    def test_recognize_long_username_with_live_invitation(
        self, bot_client, hr_client, employee
    ):
        """Длинное имя не должно доходить до базы даже при совпадении."""
        _invite(hr_client, employee)
        name = "u" * 300
        TelegramLinkInvitation.objects.update(expected_username=name[:255])
        response = bot_client.post(
            f"{API}/telegram/bot/recognize",
            {"telegram_user_id": 1, "telegram_chat_id": 1, "telegram_username": name},
            format="json", **SECRET,
        )
        assert response.status_code in (400, 409)

    @pytest.mark.parametrize(
        "value", [BIG, 0, -1, "abc", None, [], "1" * 5000]
    )
    def test_accept(self, bot_client, telegram_settings, value):
        response = bot_client.post(
            f"{API}/telegram/bot/link/accept", {"telegram_user_id": value},
            format="json", **SECRET,
        )
        assert response.status_code in (400, 409), response.content

    def test_non_json_body(self, bot_client, telegram_settings):
        response = bot_client.post(
            f"{API}/telegram/bot/link", "{not json", content_type="application/json",
            **SECRET,
        )
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "item",
        [{"id": "not-a-uuid", "sent": True}, {"id": "x", "sent": False, "error": "e" * 10_000},
         {"id": 12345, "sent": True}, {"id": ["a"], "sent": True}],
    )
    def test_outbox_report_with_bad_ids(self, bot_client, telegram_settings, item):
        response = bot_client.post(
            f"{API}/telegram/bot/outbox", {"results": [item]}, format="json", **SECRET
        )
        assert response.status_code in (200, 400), response.status_code

    def test_deeply_nested_json(self, bot_client, telegram_settings):
        """(было: RecursionError в JSONParser -> 500; закрыто зоной generic
        в humotech/core/exceptions.py, здесь — регрессия для бот-входа.)"""
        nested = "[" * 5000 + "]" * 5000
        response = bot_client.post(
            f"{API}/telegram/bot/link", nested, content_type="application/json",
            **SECRET,
        )
        assert response.status_code == 400


# --- 5a. Токен Mini App: подписанные поля сверяются с привязкой --------------


class TestMiniAppResolveDefence:
    def _token(self, account, **over):
        from humotech.telegram.tokens import MiniAppClaims, issue_mini_app_token

        claims = {
            "telegram_account_id": str(account.id),
            "telegram_user_id": account.telegram_user_id,
            "employee_id": str(account.employee_id),
            "organization_id": str(account.organization_id),
        }
        claims.update(over)
        return issue_mini_app_token(MiniAppClaims(**claims))

    def test_matching_token_resolves(self, linked_account):
        from humotech.telegram.services import TelegramMiniAppService

        assert TelegramMiniAppService().resolve(self._token(linked_account)) is not None

    @pytest.mark.parametrize("field", ["employee_id", "organization_id"])
    def test_foreign_claims_are_refused(self, linked_account, field):
        from humotech.telegram.services import TelegramMiniAppService

        token = self._token(linked_account, **{field: str(uuid.uuid4())})
        assert TelegramMiniAppService().resolve(token) is None

    def test_token_older_than_link_is_refused(self, linked_account):
        from humotech.telegram.services import TelegramMiniAppService

        token = self._token(linked_account)
        TelegramAccount.objects.filter(pk=linked_account.pk).update(
            connected_at=datetime.now(tz=dt_timezone.utc) + timedelta(minutes=5)
        )
        assert TelegramMiniAppService().resolve(token) is None


# --- 6. Чужие идентификаторы в callback — сервер проверяет владельца ---------


class TestForeignIdsFromCallbacks:
    """Бот подставляет ID из callback_data в путь. Сервер обязан проверить,
    что объект принадлежит сотруднику из X-Telegram-User-Id."""

    def test_foreign_absence_request(
        self, bot_client, linked_account, other_employee, make_absence
    ):
        foreign = make_absence(other_employee, day=date(2026, 9, 1)).origin_request
        headers = bot_headers(linked_account.telegram_user_id)
        for path in (
            f"/me/absences/{foreign.id}",
            f"/me/absences/{foreign.id}/application",
            f"/me/absences/{foreign.id}/document",
        ):
            response = bot_client.get(f"{API}{path}", **headers)
            assert response.status_code == 404, (path, response.status_code)

        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = bot_client.post(
            f"{API}/me/absences/{foreign.id}/document",
            {"document": SimpleUploadedFile(
                "spravka.pdf", b"%PDF-1.4\n%fake\n", content_type="application/pdf"
            )},
            format="multipart", **headers,
        )
        assert upload.status_code == 404

    @pytest.mark.parametrize(
        "path",
        [
            "/me/absences/{id}",
            "/me/questions/replies/{id}/file",
            "/me/policies/{id}",
            "/me/policies/{id}/file",
            "/me/surveys/{id}",
        ],
    )
    def test_random_ids_are_not_found(self, bot_client, linked_account, path):
        response = bot_client.get(
            f"{API}{path.format(id=uuid.uuid4())}",
            **bot_headers(linked_account.telegram_user_id),
        )
        assert response.status_code == 404

    def test_foreign_onboarding_ids(self, bot_client, linked_account):
        headers = bot_headers(linked_account.telegram_user_id)
        for path, body in (
            ("/me/onboarding/acknowledge", {"section_id": str(uuid.uuid4())}),
            ("/me/onboarding/acknowledge", {"section_id": "../../x"}),
            ("/me/onboarding/decision",
             {"version_id": str(uuid.uuid4()), "decision": "ACCEPTED"}),
            ("/me/onboarding/decision",
             {"version_id": "not-a-uuid", "decision": "ACCEPTED"}),
        ):
            response = bot_client.post(f"{API}{path}", body, format="json", **headers)
            assert response.status_code in (400, 403, 404, 409), (path, body, response.status_code)
