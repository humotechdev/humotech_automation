"""Привязка Telegram: ссылка, переход, решение HR.

Проверяется не «работает ли счастливый путь», а границы, ради которых схема
устроена именно так: одноразовость, срок, отзыв, изоляция организаций,
область видимости HR и то, что переход по ссылке САМ ПО СЕБЕ доступа не даёт.

Ни одного обращения в Telegram здесь нет: переход по ссылке — это вызов
сервиса, а подпись Mini App считается локальным HMAC.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest

from django_tests.conftest import build_init_data
from humotech.audit.models import AuditLog
from humotech.core.errors import Conflict, NotFound, PermissionDenied
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation
from humotech.telegram.services import (
    TelegramLinkError,
    TelegramLinkService,
    TelegramMiniAppService,
)
from humotech.telegram.tokens import hash_invitation_token, parse_start_payload

pytestmark = pytest.mark.django_db

TELEGRAM_FULL = ("employees.read", "telegram.read", "telegram.manage")
TELEGRAM_READONLY = ("employees.read", "telegram.read")


@pytest.fixture()
def hr(make_actor, organization, telegram_settings):
    """HR с правом управлять привязками по всей организации."""
    return make_actor(organization, permissions=TELEGRAM_FULL)


@pytest.fixture()
def foreign_hr(make_actor, other_organization, telegram_settings):
    """Полноправный HR, но в соседней организации.

    Именно с правами: тест изоляции обязан упереться в границу организации,
    а не в отсутствие разрешения — иначе он проверял бы не то.
    """
    return make_actor(other_organization, permissions=TELEGRAM_FULL)


@pytest.fixture()
def service() -> TelegramLinkService:
    return TelegramLinkService()


def _link(service, hr, employee, **consume):
    """Полный путь до подтверждённой привязки. Используется там, где важно
    состояние ПОСЛЕ привязки, а не она сама."""
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token,
        telegram_user_id=consume.get("telegram_user_id", 777_000_111),
        telegram_chat_id=consume.get("telegram_chat_id", 777_000_111),
        telegram_username=consume.get("telegram_username", "ivan"),
    )
    return service.confirm(hr, issued.invitation.id), issued


# --- выдача ссылки ---------------------------------------------------------

def test_invitation_stores_only_the_hash(service, hr, employee):
    """Открытого токена в базе нет ни в одном поле.

    Это главное свойство всей схемы: утечка дампа не даёт ни одной рабочей
    ссылки. Проверяем не «поле называется token_hash», а что значение
    токена не встречается в записи вообще.
    """
    issued = service.create_invitation(hr, employee.id)
    row = TelegramLinkInvitation.objects.get(id=issued.invitation.id)

    assert row.token_hash == hash_invitation_token(issued.token)
    assert row.token_hash != issued.token
    stored = {
        field.name: str(getattr(row, field.name))
        for field in TelegramLinkInvitation._meta.fields
    }
    assert issued.token not in stored.values()


def test_link_contains_no_personal_data(service, hr, employee, organization):
    """В ссылке нет ни сотрудника, ни организации — только случайный токен."""
    issued = service.create_invitation(hr, employee.id)

    assert str(employee.id) not in issued.link
    assert str(organization.id) not in issued.link
    assert employee.employee_number not in issued.link
    assert issued.link.startswith("https://t.me/humotech_test_bot?start=link_")
    assert parse_start_payload(issued.link.split("?start=")[1]) == issued.token


def test_two_invitations_are_never_the_same(service, hr, employee):
    first = service.create_invitation(hr, employee.id)
    service.revoke_invitation(hr, first.invitation.id)
    second = service.create_invitation(hr, employee.id)
    assert first.token != second.token


def test_only_hr_with_permission_can_invite(service, make_actor, organization,
                                            employee, telegram_settings):
    """Права `employees.read` мало: открыть человеку вход — отдельное решение."""
    reader = make_actor(organization, permissions=TELEGRAM_READONLY)
    with pytest.raises(PermissionDenied, match="telegram.manage"):
        service.create_invitation(reader, employee.id)


def test_hr_cannot_invite_employee_outside_scope(
    service, make_actor, organization, employee, other_office, telegram_settings
):
    """HR чужого офиса не может выдать ссылку.

    Область считается на сервере по `user_role_scopes`: переданный клиентом
    идентификатор организации в решении не участвует вовсе.
    """
    local_hr = make_actor(
        organization, permissions=TELEGRAM_FULL, office=other_office
    )
    with pytest.raises(PermissionDenied, match="вне вашей области"):
        service.create_invitation(local_hr, employee.id)


def test_foreign_organization_looks_like_a_missing_employee(
    service, foreign_hr, employee, telegram_settings
):
    """Сотрудник соседней организации отвечает как несуществующий.

    Иначе перебором идентификаторов можно пересчитать чужой штат.
    """
    with pytest.raises(NotFound, match="Сотрудник не найден"):
        service.create_invitation(foreign_hr, employee.id)


def test_terminated_employee_cannot_be_invited(service, hr, employee):
    employee.employment_status = "TERMINATED"
    employee.termination_date = date(2025, 1, 31)
    employee.save()
    with pytest.raises(Conflict, match="не работает"):
        service.create_invitation(hr, employee.id)


def test_second_live_invitation_is_refused(service, hr, employee):
    """Две рабочие ссылки у одного человека — это потеря контроля:
    HR перестаёт знать, какая из них у сотрудника на руках."""
    service.create_invitation(hr, employee.id)
    with pytest.raises(Conflict, match="действующая ссылка"):
        service.create_invitation(hr, employee.id)


def test_expired_invitation_frees_the_slot(service, hr, employee):
    """Просроченная ссылка не должна мешать выдать новую.

    Срок держится на `expires_at`, а место занимает СТАТУС — значит,
    просрочку надо материализовать, иначе сотрудник остаётся без ссылки
    навсегда.
    """
    issued = service.create_invitation(hr, employee.id)
    TelegramLinkInvitation.objects.filter(id=issued.invitation.id).update(
        expires_at=datetime.now(tz=dt_timezone.utc) - timedelta(minutes=1)
    )

    fresh = service.create_invitation(hr, employee.id)
    assert fresh.token != issued.token
    assert (
        TelegramLinkInvitation.objects.get(id=issued.invitation.id).status
        == "EXPIRED"
    )


def test_invitation_for_linked_employee_is_refused(service, hr, employee):
    """Пока привязка жива, выдача новой ссылки означала бы смену владельца
    доступа втихую. Сначала отключить, потом приглашать."""
    _link(service, hr, employee)
    with pytest.raises(Conflict, match="уже есть привязка"):
        service.create_invitation(hr, employee.id)


# --- переход по ссылке -----------------------------------------------------

def test_consume_creates_pending_binding_not_access(service, hr, employee):
    """Переход по ссылке НЕ даёт доступа. Это и есть весь смысл шага."""
    issued = service.create_invitation(hr, employee.id)
    account = service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )

    assert account.status == "PENDING"
    assert account.employee_id == employee.id
    assert (
        TelegramLinkInvitation.objects.get(id=issued.invitation.id).status
        == "PENDING_CONFIRMATION"
    )


def test_used_token_cannot_be_used_twice(service, hr, employee):
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )
    with pytest.raises(TelegramLinkError) as exc:
        service.consume(
            token=issued.token, telegram_user_id=777_2, telegram_chat_id=777_2
        )
    assert exc.value.reason == "pending"


def test_confirmed_token_cannot_be_used_again(service, hr, employee):
    _, issued = _link(service, hr, employee)
    with pytest.raises(TelegramLinkError) as exc:
        service.consume(
            token=issued.token, telegram_user_id=777_2, telegram_chat_id=777_2
        )
    assert exc.value.reason == "used"


def test_revoked_token_does_not_work(service, hr, employee):
    issued = service.create_invitation(hr, employee.id)
    service.revoke_invitation(hr, issued.invitation.id)
    with pytest.raises(TelegramLinkError) as exc:
        service.consume(
            token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
        )
    assert exc.value.reason == "revoked"


def test_expired_token_does_not_work(service, hr, employee):
    issued = service.create_invitation(hr, employee.id)
    TelegramLinkInvitation.objects.filter(id=issued.invitation.id).update(
        expires_at=datetime.now(tz=dt_timezone.utc) - timedelta(seconds=1)
    )
    with pytest.raises(TelegramLinkError) as exc:
        service.consume(
            token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
        )
    assert exc.value.reason == "expired"


def test_unknown_token_is_indistinguishable_from_a_forged_one(service):
    with pytest.raises(TelegramLinkError) as exc:
        service.consume(
            token="совершенно-выдуманный-токен",
            telegram_user_id=777_1,
            telegram_chat_id=777_1,
        )
    assert exc.value.reason == "invalid"


def test_telegram_of_another_employee_is_a_visible_conflict(
    service, hr, employee, organization, office
):
    """Один Telegram — один сотрудник. Попытка привязать занятый аккаунт
    отвечает конфликтом, а не молча переносит доступ."""
    _link(service, hr, employee, telegram_user_id=555_000)

    colleague = Employee.objects.create(
        organization=organization, employee_number="EMP-0002",
        first_name="Пётр", last_name="Петров",
        hire_date=date(2024, 3, 1), employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=colleague, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE",
        is_primary=True, valid_from=date(2024, 3, 1),
    )
    issued = service.create_invitation(hr, colleague.id)

    with pytest.raises(TelegramLinkError) as exc:
        service.consume(
            token=issued.token, telegram_user_id=555_000, telegram_chat_id=555_000
        )
    assert exc.value.reason == "telegram_taken"
    assert TelegramAccount.objects.filter(employee=colleague).count() == 0


def test_terminated_between_invite_and_click_is_refused(service, hr, employee):
    """Человек мог уволиться, пока ссылка лежала у него в переписке."""
    issued = service.create_invitation(hr, employee.id)
    employee.employment_status = "TERMINATED"
    employee.termination_date = date(2025, 6, 1)
    employee.save()

    with pytest.raises(TelegramLinkError) as exc:
        service.consume(
            token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
        )
    assert exc.value.reason == "employee_inactive"


# --- решение HR ------------------------------------------------------------

def test_confirmation_turns_pending_into_active(service, hr, employee):
    account, issued = _link(service, hr, employee)
    assert account.status == "ACTIVE"
    assert (
        TelegramLinkInvitation.objects.get(id=issued.invitation.id).status == "USED"
    )


def test_rejection_closes_both_invitation_and_binding(service, hr, employee):
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )
    account = service.reject(hr, issued.invitation.id)

    assert account.status == "REVOKED"
    assert (
        TelegramLinkInvitation.objects.get(id=issued.invitation.id).status
        == "REJECTED"
    )


def test_confirmation_requires_manage_permission(
    service, make_actor, organization, employee, hr, telegram_settings
):
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )
    reader = make_actor(organization, permissions=TELEGRAM_READONLY)
    with pytest.raises(PermissionDenied, match="telegram.manage"):
        service.confirm(reader, issued.invitation.id)


def test_disconnect_closes_access_and_open_links(service, hr, employee):
    account, issued = _link(service, hr, employee)
    disconnected = service.disconnect(hr, employee.id)

    assert disconnected.status == "REVOKED"
    assert disconnected.revoked_at is not None
    # ссылка уже USED — новых живых ссылок не остаётся
    assert not TelegramLinkInvitation.objects.filter(
        employee=employee, status__in=("ACTIVE", "PENDING_CONFIRMATION")
    ).exists()


def test_new_link_after_disconnect_works(service, hr, employee):
    """Перепривязка — обычная операция: телефон сменился, аккаунт новый."""
    _link(service, hr, employee, telegram_user_id=111_000)
    service.disconnect(hr, employee.id)

    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=222_000, telegram_chat_id=222_000
    )
    account = service.confirm(hr, issued.invitation.id)

    assert account.status == "ACTIVE"
    assert account.telegram_user_id == 222_000
    assert TelegramAccount.objects.filter(employee=employee).count() == 1


def test_disconnect_twice_is_refused(service, hr, employee):
    _link(service, hr, employee)
    service.disconnect(hr, employee.id)
    with pytest.raises(Conflict, match="уже не действует"):
        service.disconnect(hr, employee.id)


# --- что видит HR ----------------------------------------------------------

def test_status_walks_through_all_states(service, hr, employee):
    assert service.status(hr, employee.id).state == "NOT_LINKED"

    issued = service.create_invitation(hr, employee.id)
    assert service.status(hr, employee.id).state == "INVITED"

    service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )
    assert service.status(hr, employee.id).state == "PENDING"

    service.confirm(hr, issued.invitation.id)
    assert service.status(hr, employee.id).state == "ACTIVE"

    service.disconnect(hr, employee.id)
    assert service.status(hr, employee.id).state == "REVOKED"


def test_pending_queue_respects_scope(
    service, hr, employee, organization, other_office, make_actor,
    telegram_settings,
):
    """HR чужого офиса не видит чужие заявки в очереди на подтверждение."""
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )

    assert len(service.pending(hr).items) == 1

    stranger = TelegramLinkService()
    local_hr = make_actor(
        organization, permissions=TELEGRAM_FULL, office=other_office
    )
    assert stranger.pending(local_hr).items == []


def test_organizations_do_not_see_each_other(
    service, hr, employee, foreign_hr, telegram_settings
):
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )

    other = TelegramLinkService()
    assert other.pending(foreign_hr).items == []
    with pytest.raises(NotFound, match="Приглашение не найдено"):
        other.confirm(foreign_hr, issued.invitation.id)


# --- журнал ----------------------------------------------------------------

def test_every_step_lands_in_the_audit_log(service, hr, employee):
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )
    service.confirm(hr, issued.invitation.id)
    service.disconnect(hr, employee.id)

    actions = list(
        AuditLog.objects.filter(action__startswith="telegram.").values_list(
            "action", flat=True
        )
    )
    for expected in (
        "telegram.invitation.create",
        "telegram.link.consume",
        "telegram.link.confirm",
        "telegram.link.disconnect",
    ):
        assert expected in actions, f"в журнале нет {expected}"


def test_consume_is_recorded_as_the_employee_not_a_crm_user(
    service, hr, employee
):
    """Переход по ссылке совершает сотрудник, а не пользователь CRM.

    Записать это на HR значило бы обвинить его в чужом действии.
    """
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )
    entry = AuditLog.objects.get(action="telegram.link.consume")

    assert entry.actor_employee_id == employee.id
    assert entry.actor_user_id is None


def test_no_secret_ever_reaches_the_audit_log(service, hr, employee):
    """Ни открытого токена, ни хеша в журнале быть не должно."""
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=777_1, telegram_chat_id=777_1
    )
    service.confirm(hr, issued.invitation.id)

    dumped = "".join(
        f"{entry.old_values}{entry.new_values}"
        for entry in AuditLog.objects.filter(action__startswith="telegram.")
    )
    assert issued.token not in dumped
    assert hash_invitation_token(issued.token) not in dumped
    assert "token" not in dumped


# --- вход в Mini App -------------------------------------------------------

def test_active_binding_authenticates(service, hr, employee, telegram_settings):
    _link(service, hr, employee, telegram_user_id=900_100)
    session = TelegramMiniAppService().authenticate(
        build_init_data(telegram_user_id=900_100)
    )
    assert session.employee.id == employee.id
    assert session.token


def test_pending_binding_does_not_authenticate(
    service, hr, employee, telegram_settings
):
    """Главная проверка всего этапа: до подтверждения HR доступа нет."""
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=900_200, telegram_chat_id=900_200
    )
    with pytest.raises(PermissionDenied) as exc:
        TelegramMiniAppService().authenticate(
            build_init_data(telegram_user_id=900_200)
        )
    assert exc.value.details["reason"] == "pending_confirmation"


def test_unlinked_telegram_does_not_authenticate(telegram_settings, db):
    with pytest.raises(PermissionDenied) as exc:
        TelegramMiniAppService().authenticate(
            build_init_data(telegram_user_id=900_300)
        )
    assert exc.value.details["reason"] == "not_linked"


def test_revoked_binding_does_not_authenticate(
    service, hr, employee, telegram_settings
):
    _link(service, hr, employee, telegram_user_id=900_400)
    service.disconnect(hr, employee.id)
    with pytest.raises(PermissionDenied) as exc:
        TelegramMiniAppService().authenticate(
            build_init_data(telegram_user_id=900_400)
        )
    assert exc.value.details["reason"] == "not_linked"


def test_terminated_employee_does_not_authenticate(
    service, hr, employee, telegram_settings
):
    _link(service, hr, employee, telegram_user_id=900_500)
    employee.employment_status = "TERMINATED"
    employee.termination_date = date(2025, 7, 1)
    employee.save()

    with pytest.raises(PermissionDenied):
        TelegramMiniAppService().authenticate(
            build_init_data(telegram_user_id=900_500)
        )


def test_issued_token_stops_working_after_disconnect(
    service, hr, employee, telegram_settings
):
    """Отзыв действует немедленно, а не с истечением срока токена.

    Поэтому состояние привязки перечитывается на каждом запросе, а не
    берётся из подписи.
    """
    _link(service, hr, employee, telegram_user_id=900_600)
    mini_app = TelegramMiniAppService()
    session = mini_app.authenticate(build_init_data(telegram_user_id=900_600))
    assert mini_app.resolve(session.token) is not None

    service.disconnect(hr, employee.id)
    assert mini_app.resolve(session.token) is None


def test_token_of_a_previous_holder_does_not_survive_relinking(
    service, hr, employee, telegram_settings
):
    """Строка привязки у сотрудника одна и переиспользуется при перепривязке.

    Значит, её идентификатор НЕ удостоверяет владельца: без сверки
    `telegram_user_id` токен прежнего аккаунта продолжал бы работать после
    того, как HR отключил привязку и выдал её другому.
    """
    _link(service, hr, employee, telegram_user_id=901_000)
    mini_app = TelegramMiniAppService()
    old_token = mini_app.authenticate(
        build_init_data(telegram_user_id=901_000)
    ).token

    service.disconnect(hr, employee.id)
    issued = service.create_invitation(hr, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=902_000, telegram_chat_id=902_000
    )
    service.confirm(hr, issued.invitation.id)

    assert mini_app.resolve(old_token) is None
    new_token = mini_app.authenticate(
        build_init_data(telegram_user_id=902_000)
    ).token
    assert mini_app.resolve(new_token) is not None


def test_mini_app_token_is_not_the_invitation_token(
    service, hr, employee, telegram_settings
):
    """Два разных секрета не должны оказаться одним и тем же значением."""
    _, issued = _link(service, hr, employee, telegram_user_id=903_000)
    session = TelegramMiniAppService().authenticate(
        build_init_data(telegram_user_id=903_000)
    )
    assert session.token != issued.token
