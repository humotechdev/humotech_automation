"""REST API привязки Telegram: HR, бот и Mini App.

Проверяется вся дорога целиком — маршрут, аутентификация, сериализатор,
сервис, база. Тесты сервиса лежат отдельно; здесь важно, что HTTP-слой
ничего не теряет и не добавляет: права те же, изоляция та же, секреты
наружу не уходят.

Три входа, три способа доказать, кто обращается, и ни один не должен
работать на чужой территории — это и проверяется в первую очередь.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

from django_tests.conftest import (
    HR_FULL_PERMISSIONS,
    TEST_BOT_SECRET,
    bot_headers,
    build_init_data,
)
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation

pytestmark = pytest.mark.django_db

API = "/api/v1"
TELEGRAM_FULL = HR_FULL_PERMISSIONS + ("telegram.read", "telegram.manage")
BOT_HEADERS = {"HTTP_X_BOT_TOKEN": TEST_BOT_SECRET}


@pytest.fixture()
def hr_client(make_user, organization, telegram_settings):
    """Отдельный клиент, а не тот же самый.

    В этих тестах рядом работают три разных обращающихся: HR с сессией,
    бот с секретом и Mini App с токеном. Один клиент на всех означал бы,
    что `force_authenticate` для HR тихо действует и на запросы Mini App —
    и проверка «токеном Mini App нельзя в кадровый API» проходила бы
    по совершенно другой причине.
    """
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=make_user(organization, permissions=TELEGRAM_FULL))
    return client


def _invite(hr_client, employee) -> dict:
    response = hr_client.post(
        f"{API}/telegram/invitations/", {"employee_id": str(employee.id)}
    )
    assert response.status_code == 201, response.json()
    return response.json()


def _consume(api_client, token, telegram_user_id=770_001, **extra):
    payload = {
        "token": token,
        "telegram_user_id": telegram_user_id,
        "telegram_chat_id": telegram_user_id,
    }
    payload.update(extra)
    return api_client.post(f"{API}/telegram/bot/link", payload, **BOT_HEADERS)


# --- HR: выдача и отзыв ссылки ---------------------------------------------

def test_hr_creates_invitation_and_gets_the_link_once(hr_client, employee):
    body = _invite(hr_client, employee)

    assert body["link"].startswith("https://t.me/humotech_test_bot?start=link_")
    assert body["token"] in body["link"]
    assert body["invitation"]["status"] == "ACTIVE"
    # Токена в самом приглашении нет — ни открытого, ни хеша.
    assert "token_hash" not in body["invitation"]
    assert body["token"] not in str(body["invitation"])


def test_invitation_list_never_shows_the_token(hr_client, employee):
    _invite(hr_client, employee)
    response = hr_client.get(f"{API}/telegram/invitations/")

    assert response.status_code == 200
    dumped = str(response.json())
    assert "token" not in dumped


def test_invitation_needs_the_telegram_permission(
    api_client, make_user, organization, employee, telegram_settings
):
    """Кадровых прав недостаточно: открыть человеку вход — отдельное решение."""
    api_client.force_authenticate(
        user=make_user(organization, permissions=HR_FULL_PERMISSIONS)
    )
    response = api_client.post(
        f"{API}/telegram/invitations/", {"employee_id": str(employee.id)}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_invitation_outside_scope_is_refused(
    api_client, make_user, organization, employee, other_office, telegram_settings
):
    api_client.force_authenticate(
        user=make_user(
            organization, permissions=TELEGRAM_FULL, office=other_office
        )
    )
    response = api_client.post(
        f"{API}/telegram/invitations/", {"employee_id": str(employee.id)}
    )
    assert response.status_code == 403


def test_employee_of_another_organization_is_not_found(
    api_client, make_user, other_organization, employee, telegram_settings
):
    """Область считается на сервере. Идентификатор организации от клиента
    в решении не участвует — его просто негде передать."""
    api_client.force_authenticate(
        user=make_user(other_organization, permissions=TELEGRAM_FULL)
    )
    response = api_client.post(
        f"{API}/telegram/invitations/", {"employee_id": str(employee.id)}
    )
    assert response.status_code == 404


def test_revoked_invitation_stops_working(hr_client, api_client, employee):
    body = _invite(hr_client, employee)
    revoke = hr_client.post(
        f"{API}/telegram/invitations/{body['invitation']['id']}/revoke/"
    )
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "REVOKED"

    response = _consume(api_client, body["token"])
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "revoked"


# --- бот -------------------------------------------------------------------

def test_bot_endpoint_is_closed_without_the_shared_secret(
    hr_client, api_client, employee
):
    """Без секрета кто угодно привязал бы к найденной ссылке ЧУЖОЙ Telegram."""
    body = _invite(hr_client, employee)
    response = api_client.post(
        f"{API}/telegram/bot/link",
        {
            "token": body["token"],
            "telegram_user_id": 770_001,
            "telegram_chat_id": 770_001,
        },
    )
    assert response.status_code == 403
    assert not TelegramAccount.objects.exists()


def test_bot_endpoint_rejects_a_wrong_secret(hr_client, api_client, employee):
    body = _invite(hr_client, employee)
    response = api_client.post(
        f"{API}/telegram/bot/link",
        {
            "token": body["token"],
            "telegram_user_id": 770_001,
            "telegram_chat_id": 770_001,
        },
        HTTP_X_BOT_TOKEN="почти-правильный-секрет",
    )
    assert response.status_code == 403
    assert not TelegramAccount.objects.exists()


def test_bot_consumes_the_link_into_pending(hr_client, api_client, employee):
    body = _invite(hr_client, employee)
    response = _consume(api_client, body["token"], telegram_username="ivan")

    assert response.status_code == 201
    assert response.json()["status"] == "PENDING"
    assert TelegramAccount.objects.get(employee=employee).status == "PENDING"


def test_bot_accepts_terms_and_activates_the_link(hr_client, api_client, employee):
    body = _invite(hr_client, employee)
    _consume(api_client, body["token"], telegram_user_id=770_777)

    response = api_client.post(
        f"{API}/telegram/bot/link/accept",
        {"telegram_user_id": 770_777},
        **BOT_HEADERS,
    )

    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "ACTIVE"
    assert TelegramAccount.objects.get(employee=employee).status == "ACTIVE"


def test_bot_gets_a_readable_reason_for_every_failure(
    hr_client, api_client, employee
):
    """Бот обязан различать причины: человеку надо написать разное."""
    body = _invite(hr_client, employee)
    TelegramLinkInvitation.objects.filter(id=body["invitation"]["id"]).update(
        expires_at=datetime.now(tz=dt_timezone.utc) - timedelta(seconds=1)
    )
    expired = _consume(api_client, body["token"])
    assert expired.status_code == 409
    assert expired.json()["error"]["details"]["reason"] == "expired"

    unknown = _consume(api_client, "выдуманный-токен")
    assert unknown.status_code == 409
    assert unknown.json()["error"]["details"]["reason"] == "invalid"


def test_bot_cannot_pass_an_employee_id(hr_client, api_client, employee):
    """Сотрудник определяется токеном, а не тем, что прислал бот.

    Лишние поля сериализатор просто не читает: подделать можно только то,
    что где-то принимается.
    """
    body = _invite(hr_client, employee)
    response = _consume(api_client, body["token"])
    assert response.status_code == 201
    assert "employee_id" not in response.json()


# --- HR: подтверждение -----------------------------------------------------

def test_pending_queue_and_confirmation(hr_client, api_client, employee):
    body = _invite(hr_client, employee)
    _consume(api_client, body["token"], telegram_user_id=770_777)

    pending = hr_client.get(f"{API}/telegram/invitations/pending/")
    assert pending.status_code == 200
    items = pending.json()["items"]
    assert len(items) == 1
    assert items[0]["consumed_by_telegram_user_id"] == 770_777
    # HR обязан видеть, КТО перешёл: именно это решение он и принимает.
    assert items[0]["employee_name"]

    confirm = hr_client.post(
        f"{API}/telegram/invitations/{body['invitation']['id']}/confirm/"
    )
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "ACTIVE"


def test_rejection_leaves_no_access(hr_client, api_client, employee):
    body = _invite(hr_client, employee)
    _consume(api_client, body["token"])

    response = hr_client.post(
        f"{API}/telegram/invitations/{body['invitation']['id']}/reject/"
    )
    assert response.status_code == 200
    assert response.json()["status"] == "REVOKED"


def test_employee_card_shows_the_link_state(hr_client, api_client, employee):
    assert (
        hr_client.get(f"{API}/employees/{employee.id}/telegram").json()["state"]
        == "NOT_LINKED"
    )

    body = _invite(hr_client, employee)
    _consume(api_client, body["token"])
    hr_client.post(f"{API}/telegram/invitations/{body['invitation']['id']}/confirm/")

    card = hr_client.get(f"{API}/employees/{employee.id}/telegram").json()
    assert card["state"] == "ACTIVE"
    assert card["account"]["status"] == "ACTIVE"
    assert "token" not in str(card)


def test_disconnect_and_relink(hr_client, api_client, employee):
    body = _invite(hr_client, employee)
    _consume(api_client, body["token"], telegram_user_id=770_100)
    hr_client.post(f"{API}/telegram/invitations/{body['invitation']['id']}/confirm/")

    response = hr_client.post(f"{API}/employees/{employee.id}/telegram/disconnect")
    assert response.status_code == 200
    assert response.json()["status"] == "REVOKED"

    again = _invite(hr_client, employee)
    _consume(api_client, again["token"], telegram_user_id=770_200)
    confirm = hr_client.post(
        f"{API}/telegram/invitations/{again['invitation']['id']}/confirm/"
    )
    assert confirm.status_code == 200
    assert confirm.json()["telegram_user_id"] == 770_200


# --- Mini App --------------------------------------------------------------

def _link_employee(hr_client, api_client, employee, telegram_user_id):
    body = _invite(hr_client, employee)
    _consume(api_client, body["token"], telegram_user_id=telegram_user_id)
    hr_client.post(f"{API}/telegram/invitations/{body['invitation']['id']}/confirm/")


def test_mini_app_exchanges_init_data_for_a_token(
    hr_client, api_client, employee
):
    _link_employee(hr_client, api_client, employee, 880_100)

    response = api_client.post(
        f"{API}/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=880_100)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["employee"]["id"] == str(employee.id)
    # Ни строки Telegram, ни токена бота в ответе быть не может.
    assert "init_data" not in str(body)


def test_mini_app_rejects_a_forged_signature(hr_client, api_client, employee):
    _link_employee(hr_client, api_client, employee, 880_200)

    response = api_client.post(
        f"{API}/telegram/mini-app/auth",
        {
            "init_data": build_init_data(
                telegram_user_id=880_200, bot_token="999:NOT-OUR-BOT"
            )
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "init_data_rejected"


def test_mini_app_refuses_a_pending_binding(hr_client, api_client, employee):
    """Ключевая проверка этапа: до подтверждения HR входа нет."""
    body = _invite(hr_client, employee)
    _consume(api_client, body["token"], telegram_user_id=880_300)

    response = api_client.post(
        f"{API}/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=880_300)},
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "pending_confirmation"


def test_mini_app_refuses_an_unlinked_telegram(api_client, telegram_settings):
    response = api_client.post(
        f"{API}/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=880_400)},
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "not_linked"


def test_mini_app_token_opens_only_its_own_data(hr_client, api_client, employee):
    _link_employee(hr_client, api_client, employee, 880_500)
    token = api_client.post(
        f"{API}/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=880_500)},
    ).json()["access_token"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = api_client.get(f"{API}/telegram/mini-app/me")
    assert response.status_code == 200
    assert response.json()["employee"]["id"] == str(employee.id)


def test_mini_app_token_does_not_open_the_hr_api(hr_client, api_client, employee):
    """Токен Mini App не должен работать в кадровом API даже случайно.

    Класс аутентификации подключён только к своим view — на остальных
    заголовок просто некому разобрать, и запрос остаётся неопознанным.
    """
    _link_employee(hr_client, api_client, employee, 880_600)
    token = api_client.post(
        f"{API}/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=880_600)},
    ).json()["access_token"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    for path in (f"{API}/employees/", f"{API}/offices/", f"{API}/auth/me"):
        assert api_client.get(path).status_code == 403, path


def test_mini_app_without_a_token_is_unauthorized(api_client, telegram_settings):
    response = api_client.get(f"{API}/telegram/mini-app/me")
    assert response.status_code == 401
    # Схема названа: клиент должен понимать, чем авторизоваться.
    assert response["WWW-Authenticate"] == "Bearer"


def test_mini_app_token_dies_with_the_binding(hr_client, api_client, employee):
    _link_employee(hr_client, api_client, employee, 880_700)
    token = api_client.post(
        f"{API}/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=880_700)},
    ).json()["access_token"]

    hr_client.post(f"{API}/employees/{employee.id}/telegram/disconnect")

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api_client.get(f"{API}/telegram/mini-app/me").status_code == 401


# --- CORS ------------------------------------------------------------------

def test_preflight_is_answered_for_the_allowed_origin(api_client, telegram_settings):
    response = api_client.options(
        f"{API}/telegram/mini-app/auth", HTTP_ORIGIN="https://mini.humotech.tj"
    )
    assert response.status_code == 204
    assert response["Access-Control-Allow-Origin"] == "https://mini.humotech.tj"
    assert "authorization" in response["Access-Control-Allow-Headers"]
    # Cookie не разрешаем: Mini App ходит с токеном в заголовке.
    assert "Access-Control-Allow-Credentials" not in response


def test_unknown_origin_gets_no_cors_headers(api_client, telegram_settings):
    response = api_client.options(
        f"{API}/telegram/mini-app/auth", HTTP_ORIGIN="https://злой-сайт.example"
    )
    assert "Access-Control-Allow-Origin" not in response


def test_cors_does_not_leak_onto_the_hr_api(api_client, telegram_settings):
    """Разрешение выдано одному префиксу пути, а не всему API."""
    response = api_client.options(
        f"{API}/employees/", HTTP_ORIGIN="https://mini.humotech.tj"
    )
    assert "Access-Control-Allow-Origin" not in response


# --- ограничение частоты ---------------------------------------------------

def test_mini_app_auth_is_rate_limited(api_client, monkeypatch, telegram_settings):
    """Перебор подписи должен упираться в предел, а не в терпение сервера.

    Предел подменяется прямо в классе: DRF читает `DEFAULT_THROTTLE_RATES`
    один раз при импорте и в атрибут класса, поэтому правка настроек во
    время теста до него уже не доходит.
    """
    from rest_framework.throttling import ScopedRateThrottle

    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {**ScopedRateThrottle.THROTTLE_RATES, "telegram_mini_app": "3/min"},
    )
    codes = [
        api_client.post(
            f"{API}/telegram/mini-app/auth",
            {"init_data": build_init_data(telegram_user_id=890_000 + n)},
        ).status_code
        for n in range(5)
    ]
    assert 429 in codes, codes


# --- шов с ботом -----------------------------------------------------------
#
# Ниже — не ещё одна проверка привязки, а фиксация КОНТРАКТА между двумя
# приложениями: путь, имя заголовка и форма тела ошибки. Со стороны бота
# он закреплён в apps/employee-telegram-bot/tests/test_link_flow.py, здесь —
# со стороны backend. По отдельности ни одна сторона несовпадения не увидит,
# а сломается оно на первом же настоящем переходе по ссылке.

BOT_CONTRACT_PATH = "/api/v1/telegram/bot/link"
BOT_CONTRACT_HEADER = "X-Bot-Token"


def _exactly_what_the_bot_sends(token: str) -> dict:
    """Тело запроса ровно в том составе, в каком его собирает бот.

    Список полей списан с `consume_link_token` в клиенте бота. Лишнее поле
    здесь означало бы, что backend принимает то, чего бот не шлёт, —
    и наоборот.
    """
    return {
        "token": token,
        "telegram_user_id": 555,
        "telegram_chat_id": 555,
        "telegram_username": "ivan",
        "language_code": "ru",
    }


def test_bot_contract_path_and_header(hr_client, api_client, employee):
    body = _invite(hr_client, employee)
    response = api_client.post(
        BOT_CONTRACT_PATH,
        _exactly_what_the_bot_sends(body["token"]),
        **{f"HTTP_{BOT_CONTRACT_HEADER.upper().replace('-', '_')}": TEST_BOT_SECRET},
    )
    assert response.status_code == 201, response.json()
    assert response.json()["status"] == "PENDING"


def test_bot_contract_error_body_carries_the_reason(hr_client, api_client, employee):
    """Бот выбирает текст по `error.details.reason`.

    Если причина переедет в другое место тела, бот молча начнёт отвечать
    «сервис недоступен» на каждый понятный отказ.
    """
    body = _invite(hr_client, employee)
    hr_client.post(f"{API}/telegram/invitations/{body['invitation']['id']}/revoke/")

    response = api_client.post(
        BOT_CONTRACT_PATH,
        _exactly_what_the_bot_sends(body["token"]),
        **{f"HTTP_{BOT_CONTRACT_HEADER.upper().replace('-', '_')}": TEST_BOT_SECRET},
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == "conflict"
    assert error["details"]["reason"] == "revoked"


@pytest.mark.django_db
def test_resend_replaces_the_live_invitation(hr_client, employee, telegram_settings):
    """«Отправить повторно» заменяет ссылку, а не упирается в неё.

    Сотруднику ссылку выдают сразу при приёме. Без замены повтор всегда
    натыкался бы на собственную ссылку минутной давности, и кнопка не
    срабатывала бы никогда.
    """
    from humotech.telegram.models import TelegramLinkInvitation

    first = hr_client.post(
        f"{API}/telegram/invitations/", {"employee_id": str(employee.id)},
        format="json",
    )
    assert first.status_code == 201, first.content

    # Без явной замены повтор по-прежнему отказывает: две живые ссылки на
    # одного человека — это две двери.
    refused = hr_client.post(
        f"{API}/telegram/invitations/", {"employee_id": str(employee.id)},
        format="json",
    )
    assert refused.status_code == 409

    again = hr_client.post(
        f"{API}/telegram/invitations/",
        {"employee_id": str(employee.id), "replace": True},
        format="json",
    )
    assert again.status_code == 201, again.content
    assert again.json()["link"] != first.json()["link"]

    rows = {
        one.status
        for one in TelegramLinkInvitation.objects.filter(employee_id=employee.id)
    }
    assert rows == {"REVOKED", "ACTIVE"}
    assert TelegramLinkInvitation.objects.filter(
        employee_id=employee.id, status="ACTIVE"
    ).count() == 1


@pytest.mark.django_db
class TestRecognize:
    """Узнавание по имени в Telegram: когда человек открыл бота сам.

    Бот не может написать первым — это правило Telegram. Всё, что можно
    сделать, — узнать пришедшего по `@username`, который кадровик указал
    в карточке, и поздороваться с ним по имени.
    """

    def ask(self, bot_client, **over):
        body = {
            "telegram_user_id": 990_100,
            "telegram_chat_id": 990_100,
            "telegram_username": "azizbek",
            "language_code": "ru",
        }
        body.update(over)
        return bot_client.post(
            f"{API}/telegram/bot/recognize", body, format="json", **bot_headers()
        )

    def test_known_username_is_greeted_by_name(
        self, hr_client, bot_client, employee, telegram_settings,
    ):
        hr_client.post(
            f"{API}/telegram/invitations/",
            {"employee_id": str(employee.id)}, format="json",
        )
        # Имя-подсказка живёт на приглашении: кадровик указал его в карточке.
        TelegramLinkInvitation.objects.filter(employee_id=employee.id).update(
            expected_username="azizbek"
        )

        answer = self.ask(bot_client)
        assert answer.status_code == 201, answer.content
        body = answer.json()
        # Привязка ещё не рабочая: узнавание доводит до того же окна
        # подтверждения, что и ссылка.
        assert body["status"] == "PENDING"
        assert body["full_name"] == "Иванов Иван"
        assert body["office_name"]

    def test_unknown_username_is_sent_to_hr(self, bot_client, telegram_settings):
        answer = self.ask(bot_client, telegram_username="nobody")
        assert answer.status_code == 409
        assert answer.json()["error"]["details"]["reason"] == "unknown"

    def test_without_username_there_is_nothing_to_recognize(
        self, bot_client, telegram_settings,
    ):
        answer = self.ask(bot_client, telegram_username="")
        assert answer.status_code == 409
        assert answer.json()["error"]["details"]["reason"] == "no_username"

    def test_recognition_needs_a_live_invitation(
        self, bot_client, employee, telegram_settings,
    ):
        """Без действующего приглашения узнавание не работает.

        Иначе оно стало бы отдельной дорогой в обход кадровика: кто угодно
        с подходящим именем привязался бы к сотруднику, которого никто не
        приглашал.
        """
        answer = self.ask(bot_client)
        assert answer.status_code == 409
        # Без живого приглашения человека никто не звал: и узнать его,
        # и отличить от постороннего с тем же именем невозможно.
        assert answer.json()["error"]["details"]["reason"] == "unknown"

    def test_same_username_on_two_cards_is_refused(
        self, hr_client, bot_client, employee, organization, office, telegram_settings,
    ):
        """Одно имя у двух карточек — ошибка данных, а не повод гадать."""
        from datetime import date

        from humotech.employees.models import Employee

        other = Employee.objects.create(
            organization=organization, employee_number="EMP-9100",
            first_name="Пётр", last_name="Петров", hire_date=date(2024, 3, 1),
            employment_status="ACTIVE",
        )
        for one in (employee, other):
            hr_client.post(
                f"{API}/telegram/invitations/", {"employee_id": str(one.id)},
                format="json",
            )
        TelegramLinkInvitation.objects.all().update(expected_username="azizbek")

        answer = self.ask(bot_client)
        assert answer.status_code == 409
        assert answer.json()["error"]["details"]["reason"] == "ambiguous"
