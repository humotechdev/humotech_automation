"""Вход сотрудника: из Mini App и из бота, по одним и тем же правилам.

Проверяется не «работает ли профиль», а граница доступа. У бота нет токена
сотрудника — он предъявляет общий секрет и подтверждённый Telegram ID, —
поэтому вся защита держится на том, что эти две вещи проверяются вместе
и заново на каждом запросе.

Отдельная тема здесь — что именно узнаёт отказавшийся. Mini App открыт кому
угодно, и по разнице ответов из него выясняли бы, чей Telegram заведён
в системе. Бот предъявил секрет, то есть он наша же сторона, и ему причина
называется точно. Эта разница проверяется прямо.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from humotech.employees.models import EmployeeAssignment
from humotech.telegram.identity import (
    resolve_by_telegram_user_id,
)
from humotech.telegram.models import TelegramAccount

from .conftest import TEST_BOT_SECRET, bot_headers, link_telegram

pytestmark = pytest.mark.django_db

PROFILE = "/api/v1/me/profile"
TG_ID = 777_000_111


# --- вход бота: секрет и только вместе с ним ------------------------------

def test_bot_with_secret_and_telegram_id_gets_the_employee(
    bot_client, linked_account, employee
):
    response = bot_client.get(PROFILE, **bot_headers(TG_ID))

    assert response.status_code == 200
    assert response.json()["employee"]["id"] == str(employee.id)


def test_telegram_id_without_secret_is_nobody(bot_client, linked_account):
    """Главная проверка всей схемы.

    Без секрета заголовок с Telegram ID — просто число, которое написал
    отправитель запроса. Если бы его принимали, данные любого сотрудника
    получал бы любой, кто знает его Telegram ID и адрес сервера.
    """
    response = bot_client.get(PROFILE, HTTP_X_TELEGRAM_USER_ID=str(TG_ID))

    assert response.status_code == 401


def test_wrong_secret_is_refused(bot_client, linked_account):
    response = bot_client.get(
        PROFILE, **bot_headers(TG_ID, secret="почти-верный-секрет")
    )
    assert response.status_code == 401


def test_non_ascii_secret_is_refused_not_crashed(bot_client, linked_account):
    """Сравнение секретов не должно падать на кириллице.

    `hmac.compare_digest` бросает TypeError на не-ASCII строках, и без
    хеширования обеих сторон подставленный кириллический заголовок давал бы
    500 вместо отказа — с содержимым запроса в журнале ошибок.
    """
    response = bot_client.get(PROFILE, **bot_headers(TG_ID, secret="секрет"))
    assert response.status_code == 401


def test_secret_not_configured_closes_the_door(
    bot_client, linked_account, settings
):
    """Ненастроенный секрет закрывает вход, а не открывает его всем."""
    settings.TELEGRAM = {**settings.TELEGRAM, "BOT_API_SECRET": ""}

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))
    assert response.status_code == 401


def test_malformed_telegram_id_is_refused(bot_client, linked_account):
    response = bot_client.get(
        PROFILE,
        HTTP_X_BOT_TOKEN=TEST_BOT_SECRET,
        HTTP_X_TELEGRAM_USER_ID="'; DROP TABLE employees; --",
    )
    assert response.status_code == 401


def test_no_headers_at_all_is_unauthorized(bot_client, telegram_settings):
    assert bot_client.get(PROFILE).status_code == 401


# --- состояние привязки ----------------------------------------------------

def test_pending_binding_gives_no_access(bot_client, employee, telegram_settings):
    """Переход по ссылке сам по себе доступа не даёт."""
    link_telegram(employee, status="PENDING")

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))

    assert response.status_code == 401
    assert response.json()["error"]["details"]["reason"] == "pending_confirmation"


def test_revoked_binding_gives_no_access(bot_client, employee, telegram_settings):
    link_telegram(employee, status="REVOKED")

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))

    assert response.status_code == 401
    assert response.json()["error"]["details"]["reason"] == "link_revoked"


def test_blocked_binding_gives_no_access(bot_client, employee, telegram_settings):
    link_telegram(employee, status="BLOCKED")

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))

    assert response.status_code == 401
    assert response.json()["error"]["details"]["reason"] == "link_blocked"


def test_unknown_telegram_id_is_not_linked(bot_client, telegram_settings):
    response = bot_client.get(PROFILE, **bot_headers(999_999_999))

    assert response.status_code == 401
    assert response.json()["error"]["details"]["reason"] == "not_linked"


def test_revocation_takes_effect_immediately(
    bot_client, linked_account, employee
):
    """Отзыв действует со следующего запроса, а не когда-нибудь.

    Ровно ради этого у бота нет собственного токена сотрудника: отзывать
    было бы нечего, и выданный токен продолжал бы работать до истечения.
    """
    assert bot_client.get(PROFILE, **bot_headers(TG_ID)).status_code == 200

    linked_account.status = "REVOKED"
    linked_account.save(update_fields=["status", "updated_at"])

    assert bot_client.get(PROFILE, **bot_headers(TG_ID)).status_code == 401


# --- состояние сотрудника и организации ------------------------------------

@pytest.mark.parametrize("status", ["TERMINATED", "ARCHIVED"])
def test_inactive_employee_has_no_access(
    bot_client, linked_account, employee, status
):
    employee.employment_status = status
    employee.save(update_fields=["employment_status", "updated_at"])

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))

    assert response.status_code == 401
    assert response.json()["error"]["details"]["reason"] == "employee_inactive"


def test_archived_employee_has_no_access(bot_client, linked_account, employee):
    employee.archived_at = timezone.now()
    employee.save(update_fields=["archived_at", "updated_at"])

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))
    assert response.status_code == 401


def test_inactive_organization_closes_access_for_everyone(
    bot_client, linked_account, organization
):
    organization.status = "INACTIVE"
    organization.save(update_fields=["status", "updated_at"])

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))

    assert response.status_code == 401
    assert response.json()["error"]["details"]["reason"] == "organization_inactive"


def test_employee_without_current_assignment_has_no_access(
    bot_client, linked_account, employee
):
    """Без действующего назначения неизвестен офис — а с ним и часовой пояс.

    Открыть доступ «наполовину» здесь нельзя: сутки не в чем считать,
    и любая цифра статистики оказалась бы выдуманной.
    """
    EmployeeAssignment.objects.filter(employee=employee).update(
        valid_to=date.today() - timedelta(days=1)
    )

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))

    assert response.status_code == 401
    assert response.json()["error"]["details"]["reason"] == "no_assignment"


def test_future_assignment_does_not_open_access_yet(
    bot_client, linked_account, employee
):
    EmployeeAssignment.objects.filter(employee=employee).update(
        valid_from=date.today() + timedelta(days=7), valid_to=None
    )

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))
    assert response.status_code == 401


# --- что нельзя подсунуть --------------------------------------------------

def test_employee_id_in_the_body_is_ignored(
    bot_client, linked_account, employee, other_organization
):
    """Подставить чужого сотрудника нечем.

    Даже если клиент пришлёт `employee_id` и `organization_id`, читать их
    некому: сотрудник берётся из привязки, а параметров с такими именами
    нет ни в одном пути и ни в одном теле.
    """
    response = bot_client.get(
        PROFILE,
        {"employee_id": str(_someone_else(other_organization).id),
         "organization_id": str(other_organization.id)},
        **bot_headers(TG_ID),
    )

    assert response.status_code == 200
    assert response.json()["employee"]["id"] == str(employee.id)


def test_one_telegram_cannot_serve_two_employees(
    bot_client, linked_account, employee, organization, office
):
    """Второй сотрудник с тем же Telegram ID заводиться не должен."""
    from django.db import IntegrityError

    from humotech.employees.models import Employee

    twin = Employee.objects.create(
        organization=organization,
        employee_number="EMP-0002",
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2024, 3, 1),
        employment_status="ACTIVE",
    )
    with pytest.raises(IntegrityError):
        TelegramAccount.objects.create(
            organization=organization,
            employee=twin,
            telegram_user_id=TG_ID,
            telegram_chat_id=TG_ID,
            status="ACTIVE",
            connected_at=timezone.now(),
        )


# --- что видит Mini App против того, что видит бот -------------------------

def test_mini_app_cannot_tell_revoked_from_never_linked(
    api_client, employee, telegram_settings
):
    """Ответ Mini App не должен работать справочником по чужим привязкам.

    Отозванная привязка и отсутствующая отвечают одинаково. Иначе тот, кто
    открыл страницу, выяснял бы по коду ответа, чей Telegram заведён
    в системе.
    """
    from .conftest import build_init_data

    link_telegram(employee, status="REVOKED")
    revoked = api_client.post(
        "/api/v1/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=TG_ID)},
        format="json",
    )
    absent = api_client.post(
        "/api/v1/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=555_000_222)},
        format="json",
    )

    assert revoked.status_code == absent.status_code == 403
    assert (
        revoked.json()["error"]["details"]["reason"]
        == absent.json()["error"]["details"]["reason"]
        == "not_linked"
    )


def test_bot_is_told_the_exact_reason(bot_client, employee, telegram_settings):
    """А боту причина называется точно — он предъявил секрет.

    Ему выбирать формулировку человеку: «ждём подтверждения отдела кадров»
    и «привязка отключена» требуют разных действий.
    """
    link_telegram(employee, status="REVOKED")

    response = bot_client.get(PROFILE, **bot_headers(TG_ID))
    assert response.json()["error"]["details"]["reason"] == "link_revoked"


# --- профиль ---------------------------------------------------------------

def test_profile_shows_office_and_timezone(bot_client, linked_account, office):
    body = bot_client.get(PROFILE, **bot_headers(TG_ID)).json()

    assert body["office"]["name"] == office.name
    assert body["office"]["timezone"] == "Asia/Dushanbe"


def test_profile_carries_no_one_elses_identifiers(
    bot_client, linked_account, employee, organization
):
    """В ответе нет ни организации, ни чужих сотрудников.

    Организацию клиенту знать незачем: он не может ей ничего адресовать,
    а лишний идентификатор в ответе — приглашение попробовать его подставить.
    """
    raw = bot_client.get(PROFILE, **bot_headers(TG_ID)).content.decode()

    assert str(organization.id) not in raw


def test_mini_app_and_bot_see_the_same_profile(
    bot_client, api_client, linked_account, telegram_settings
):
    """Один код на два входа — значит, и ответ один.

    Разойдись они, выяснять, какой из них правильный, было бы нечем.
    """
    from .conftest import build_init_data

    token = api_client.post(
        "/api/v1/telegram/mini-app/auth",
        {"init_data": build_init_data(telegram_user_id=TG_ID)},
        format="json",
    ).json()["access_token"]

    from_mini_app = api_client.get(PROFILE, HTTP_AUTHORIZATION=f"Bearer {token}")
    from_bot = bot_client.get(PROFILE, **bot_headers(TG_ID))

    assert from_mini_app.status_code == from_bot.status_code == 200
    assert from_mini_app.json() == from_bot.json()


# --- общий шлюз ------------------------------------------------------------

def test_identity_gate_is_the_same_for_both_entrances(
    linked_account, employee
):
    """Проверка допуска одна на оба входа — не две одинаковые.

    Две копии разошлись бы на первой правке: проверку увольнения добавили бы
    в Mini App и забыли в боте.
    """
    from humotech.telegram.services import TelegramMiniAppService

    context = resolve_by_telegram_user_id(linked_account.telegram_user_id)
    assert context.employee.id == employee.id

    employee.employment_status = "TERMINATED"
    employee.save(update_fields=["employment_status", "updated_at"])

    # оба входа закрываются одним и тем же изменением
    assert resolve_by_telegram_user_id(linked_account.telegram_user_id).reason == (
        "employee_inactive"
    )
    assert TelegramMiniAppService().resolve("что-угодно-нерабочее") is None


def _someone_else(other_organization):
    from humotech.employees.models import Employee

    return Employee.objects.create(
        organization=other_organization,
        employee_number="EMP-9999",
        first_name="Чужой",
        last_name="Сотрудник",
        hire_date=date(2024, 1, 1),
        employment_status="ACTIVE",
    )
