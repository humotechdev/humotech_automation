"""Аудит безопасности: очередь уведомлений, уведомления CRM, журнал аудита.

Каждый тест — атака, которая раньше проходила или могла пройти, либо
граница, которую надо удерживать. Данные только вымышленные, секрет
бота — тестовый (`TEST_BOT_SECRET`).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta

import pytest
from django.utils import timezone

from humotech.audit.models import AuditLog, AuditLogImmutable
from humotech.audit.redaction import (
    MASK,
    SecretRedactingFilter,
    redact_text,
    redact_values,
)
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications import outbox
from humotech.notifications.models import Notification, NotificationAttempt
from humotech.telegram.models import TelegramAccount

from .conftest import TEST_BOT_SECRET, bot_headers, link_telegram

pytestmark = pytest.mark.django_db

API = "/api/v1"
OUTBOX = f"{API}/telegram/bot/outbox"
TG_ID = 777_000_111

# Похоже на токен бота по форме, но выдумано целиком.
FAKE_BOT_TOKEN = "123456789:AAFakeFakeFakeFakeFakeFakeFakeFake_x"


def bot_auth() -> dict:
    return {"HTTP_X_BOT_TOKEN": TEST_BOT_SECRET}


@pytest.fixture()
def notification_settings(settings):
    settings.NOTIFICATIONS = {
        **settings.NOTIFICATIONS,
        "MAX_ATTEMPTS": 3,
        "RETRY_BASE_SECONDS": 30,
        "RETRY_MAX_SECONDS": 600,
        "LOCK_TIMEOUT_SECONDS": 300,
        "BATCH_SIZE": 20,
    }
    return settings.NOTIFICATIONS


@pytest.fixture()
def linked(db, employee, telegram_settings, notification_settings):
    return link_telegram(employee)


def _enqueue(employee, *, kind="absence.created", body="Заявка отправлена",
             key=None) -> Notification:
    return outbox.enqueue(
        organization_id=employee.organization_id,
        employee_id=employee.id,
        notification_type=kind,
        body=body,
        idempotency_key=key,
    )


def _age_lock(row_id, *, minutes=60):
    Notification.objects.filter(id=row_id).update(
        locked_at=timezone.now() - timedelta(minutes=minutes)
    )


# =====================================================================
# reclaim_stale: зависшая строка тратит попытку, вечного цикла нет
# =====================================================================

def test_reclaim_counts_an_attempt_and_records_it(linked, employee):
    row = _enqueue(employee)
    assert len(outbox.claim()) == 1
    _age_lock(row.id)

    assert outbox.reclaim_stale() == 1

    row.refresh_from_db()
    assert row.status == "PENDING"
    assert row.attempts == 1
    assert row.locked_at is None
    # Не «прямо сейчас», а с паузой: иначе падающий отправщик
    # получает ту же строку на следующем же опросе.
    assert row.next_attempt_at > timezone.now()
    log = list(NotificationAttempt.objects.filter(notification_id=row.id))
    assert [(a.outcome, a.reason) for a in log] == [("FAILED", "stale_lock")]


def test_a_sender_that_always_dies_cannot_loop_forever(
    linked, employee, notification_settings
):
    """Отправщик падает между захватом и отчётом — каждый раз.

    Раньше строка ходила RUNNING → PENDING → RUNNING бесконечно, и
    человек получал одно и то же сообщение раз в пять минут навсегда.
    """
    row = _enqueue(employee)
    later = timezone.now()
    for _ in range(notification_settings["MAX_ATTEMPTS"] + 2):
        later += timedelta(hours=2)
        outbox.claim(now=later)
        outbox.reclaim_stale(now=later + timedelta(hours=1))

    row.refresh_from_db()
    assert row.status == "FAILED"
    assert row.attempts == notification_settings["MAX_ATTEMPTS"]
    assert outbox.claim(now=later + timedelta(days=30)) == []


def test_a_late_success_after_reclaim_prevents_a_duplicate(linked, employee):
    """Отправщик отправил, но отчитался позже уборки.

    Раньше отчёт отбрасывался (строка уже PENDING), и сообщение уходило
    второй раз. Теперь поздний успех закрывает строку.
    """
    row = _enqueue(employee)
    outbox.claim()
    _age_lock(row.id)
    outbox.reclaim_stale()

    outbox.mark_sent(row.id)

    row.refresh_from_db()
    assert row.status == "SENT"
    assert outbox.claim(now=timezone.now() + timedelta(hours=1)) == []


def test_a_late_failure_after_reclaim_is_not_counted_twice(linked, employee):
    row = _enqueue(employee)
    outbox.claim()
    _age_lock(row.id)
    outbox.reclaim_stale()

    outbox.mark_failed(row.id, error="timeout")

    row.refresh_from_db()
    assert row.attempts == 1
    assert NotificationAttempt.objects.filter(notification_id=row.id).count() == 1


def test_late_success_does_not_revive_an_operator_cancel(
    linked, employee, make_actor, organization
):
    """Кадровик снял строку после уборки — поздний отчёт её не трогает."""
    row = _enqueue(employee)
    outbox.claim()
    _age_lock(row.id)
    outbox.reclaim_stale()
    Notification.objects.filter(id=row.id).update(
        status="CANCELLED", error_message="cancelled_by_operator"
    )

    outbox.mark_sent(row.id)

    assert Notification.objects.get(id=row.id).status == "CANCELLED"


# =====================================================================
# POST /telegram/bot/outbox: повторы, чужие строки, мусор
# =====================================================================

def test_repeated_success_report_is_idempotent(api_client, linked, employee):
    row = _enqueue(employee)
    message_id = api_client.get(OUTBOX, **bot_auth()).json()["messages"][0]["id"]
    body = {"results": [{"id": message_id, "sent": True}] * 3}

    for _ in range(2):
        assert api_client.post(
            OUTBOX, body, format="json", **bot_auth()
        ).status_code == 200

    row.refresh_from_db()
    assert row.status == "SENT"
    assert NotificationAttempt.objects.filter(
        notification_id=row.id, outcome="SENT"
    ).count() == 1


def test_repeated_failure_report_counts_once(api_client, linked, employee):
    row = _enqueue(employee)
    message_id = api_client.get(OUTBOX, **bot_auth()).json()["messages"][0]["id"]
    body = {"results": [{"id": message_id, "sent": False, "error": "x"}] * 3}

    api_client.post(OUTBOX, body, format="json", **bot_auth())
    api_client.post(OUTBOX, body, format="json", **bot_auth())

    row.refresh_from_db()
    assert row.attempts == 1


@pytest.mark.parametrize("state", ["PENDING", "CANCELLED", "FAILED", "READ"])
def test_report_for_a_row_that_was_not_handed_out_changes_nothing(
    api_client, linked, employee, state
):
    """Отчёт о строке, которую бот не забирал, — не повод её закрыть."""
    row = _enqueue(employee)
    Notification.objects.filter(id=row.id).update(status=state)

    for sent in (True, False):
        api_client.post(
            OUTBOX, {"results": [{"id": str(row.id), "sent": sent}]},
            format="json", **bot_auth(),
        )

    row.refresh_from_db()
    assert row.status == state
    assert row.attempts == 0
    assert not NotificationAttempt.objects.filter(notification_id=row.id).exists()


def test_report_for_an_unknown_row_is_not_accepted(api_client, linked):
    response = api_client.post(
        OUTBOX, {"results": [{"id": str(uuid.uuid4()), "sent": True}]},
        format="json", **bot_auth(),
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 0


@pytest.mark.parametrize(
    "item",
    [
        {"id": "не-uuid", "sent": True},
        {"id": "' OR 1=1 --", "sent": False},
        {"id": {"$ne": None}, "sent": True},
        {"id": ["a", "b"], "sent": True},
        {"id": 12345, "sent": True},
        {"id": "x" * 100_000, "sent": True},
        "строка вместо объекта",
        None,
    ],
)
def test_garbage_ids_are_skipped_not_500(api_client, linked, item):
    response = api_client.post(
        OUTBOX, {"results": [item]}, format="json", **bot_auth()
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 0


@pytest.mark.parametrize("body", [[1, 2, 3], "results", 42, None])
def test_non_object_body_is_400_not_500(api_client, linked, body):
    import json

    response = api_client.generic(
        "POST", OUTBOX, json.dumps(body), content_type="application/json",
        **bot_auth(),
    )
    assert response.status_code == 400


@pytest.mark.parametrize("flag", ["false", "0", "", 1, "true"])
def test_sent_must_be_a_real_boolean(api_client, linked, employee, flag):
    """`"sent": "false"` — непустая строка; раньше считалась успехом."""
    row = _enqueue(employee)
    message_id = api_client.get(OUTBOX, **bot_auth()).json()["messages"][0]["id"]

    api_client.post(
        OUTBOX, {"results": [{"id": message_id, "sent": flag}]},
        format="json", **bot_auth(),
    )

    assert Notification.objects.get(id=row.id).status != "SENT"


def test_error_text_is_bounded_and_secret_free(api_client, linked, employee):
    row = _enqueue(employee)
    message_id = api_client.get(OUTBOX, **bot_auth()).json()["messages"][0]["id"]

    api_client.post(
        OUTBOX,
        {"results": [{"id": message_id, "sent": False,
                      "error": {"echo": FAKE_BOT_TOKEN + "я" * 10_000}}]},
        format="json", **bot_auth(),
    )

    row.refresh_from_db()
    assert len(row.error_message) <= 200
    assert FAKE_BOT_TOKEN not in row.error_message


def test_error_string_with_a_bot_token_is_masked(api_client, linked, employee):
    row = _enqueue(employee)
    message_id = api_client.get(OUTBOX, **bot_auth()).json()["messages"][0]["id"]

    api_client.post(
        OUTBOX,
        {"results": [{"id": message_id, "sent": False,
                      "error": f"POST /bot{FAKE_BOT_TOKEN}/sendMessage 502"}]},
        format="json", **bot_auth(),
    )

    row.refresh_from_db()
    assert FAKE_BOT_TOKEN not in row.error_message
    attempt = NotificationAttempt.objects.get(notification_id=row.id)
    assert FAKE_BOT_TOKEN not in attempt.reason


@pytest.mark.parametrize("code", ["attachment_rejected", "attachment_not_found"])
def test_rejected_attachment_is_a_permanent_failure(
    api_client, linked, employee, code
):
    """Файл не прошёл проверку — backend отдаёт 404. Повторять незачем."""
    row = _enqueue(employee, kind="question.reply.file")
    message_id = api_client.get(OUTBOX, **bot_auth()).json()["messages"][0]["id"]

    api_client.post(
        OUTBOX, {"results": [{"id": message_id, "sent": False, "error": code}]},
        format="json", **bot_auth(),
    )

    row.refresh_from_db()
    assert row.status == "FAILED"
    assert row.attempts == 1
    assert outbox.claim(now=timezone.now() + timedelta(days=1)) == []


def test_transient_attachment_failure_is_retried_but_bounded(
    linked, employee, notification_settings
):
    """`attachment_unavailable` (бэкенд недоступен) повторяется, но конечно."""
    row = _enqueue(employee, kind="question.reply.file")
    later = timezone.now()
    for _ in range(notification_settings["MAX_ATTEMPTS"] + 3):
        later += timedelta(hours=2)
        for item in outbox.claim(now=later):
            outbox.mark_failed(item.id, error="attachment_unavailable", now=later)

    row.refresh_from_db()
    assert row.status == "FAILED"
    assert row.attempts == notification_settings["MAX_ATTEMPTS"]


def test_report_batch_is_capped(api_client, linked):
    items = [{"id": str(uuid.uuid4()), "sent": True} for _ in range(5000)]
    response = api_client.post(
        OUTBOX, {"results": items}, format="json", **bot_auth()
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 0


# =====================================================================
# GET /telegram/bot/outbox: кто вызывает и что отдаётся
# =====================================================================

@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"HTTP_X_BOT_TOKEN": ""},
        {"HTTP_X_BOT_TOKEN": "не тот"},
        {"HTTP_X_BOT_TOKEN": TEST_BOT_SECRET + "x"},
        {"HTTP_X_TELEGRAM_USER_ID": str(TG_ID)},
        {"HTTP_AUTHORIZATION": f"Bearer {TEST_BOT_SECRET}"},
    ],
)
def test_outbox_needs_the_exact_bot_secret(api_client, linked, headers):
    assert api_client.get(OUTBOX, **headers).status_code == 403
    assert api_client.post(
        OUTBOX, {"results": []}, format="json", **headers
    ).status_code == 403


def test_outbox_is_closed_when_the_secret_is_not_configured(
    api_client, linked, settings
):
    settings.TELEGRAM = {**settings.TELEGRAM, "BOT_API_SECRET": ""}
    assert api_client.get(OUTBOX, HTTP_X_BOT_TOKEN="").status_code == 403


def test_crm_session_does_not_open_the_outbox(api_client, linked, make_user,
                                              organization):
    user = make_user(organization, permissions=("notifications.manage",))
    api_client.force_authenticate(user=user)
    assert api_client.get(OUTBOX).status_code == 403


def test_outbox_gives_only_the_fields_a_sender_needs(api_client, linked, employee):
    _enqueue(employee, body="Текст")

    body = api_client.get(OUTBOX, **bot_auth()).json()

    assert set(body) == {"messages", "reclaimed"}
    (message,) = body["messages"]
    assert set(message) == {
        "id", "chat_id", "text", "type", "attempts", "entity_id",
        "attachment", "telegram_user_id",
    }
    flat = str(message)
    for leak in ("Иван", "Иванов", "EMP-0001", str(employee.id),
                 str(employee.organization_id)):
        assert leak not in flat


@pytest.mark.parametrize(
    "limit", ["100000", "-1", "0", "abc", "1e9", "²", "%00", "1&limit=500"]
)
def test_batch_size_is_not_taken_from_the_query(
    api_client, linked, employee, settings, limit
):
    settings.NOTIFICATIONS = {**settings.NOTIFICATIONS, "BATCH_SIZE": 2}
    for n in range(4):
        _enqueue(employee, key=f"k{n}")

    response = api_client.get(f"{OUTBOX}?limit={limit}", **bot_auth())

    assert response.status_code == 200
    assert len(response.json()["messages"]) == 2


# =====================================================================
# Демо-изоляция: chat id 5000–5999 не получает ничего
# =====================================================================

@pytest.mark.parametrize("chat_id", [5000, 5500, 5999])
@pytest.mark.parametrize("kind", ["absence.created", "telegram.link.rejected"])
def test_demo_chats_never_leave_the_queue(api_client, db, employee,
                                          telegram_settings,
                                          notification_settings, chat_id, kind):
    account = link_telegram(employee, telegram_user_id=chat_id)
    if kind.startswith("telegram.link."):
        # Уведомление о привязке уходит даже отозванной — но не демо.
        TelegramAccount.objects.filter(id=account.id).update(status="REVOKED")
    row = _enqueue(employee, kind=kind)

    body = api_client.get(OUTBOX, **bot_auth()).json()

    assert body["messages"] == []
    row.refresh_from_db()
    assert row.status == "CANCELLED"
    assert row.error_message == "demo_account"


def test_demo_user_id_with_a_real_chat_is_still_demo_by_chat(
    api_client, db, employee, telegram_settings, notification_settings
):
    """Решает chat id — адрес, куда уходит сообщение, а не user id."""
    account = link_telegram(employee, telegram_user_id=900_000_001)
    TelegramAccount.objects.filter(id=account.id).update(telegram_chat_id=5100)
    _enqueue(employee)

    assert api_client.get(OUTBOX, **bot_auth()).json()["messages"] == []


def test_just_outside_the_demo_range_is_delivered(
    api_client, db, employee, telegram_settings, notification_settings
):
    link_telegram(employee, telegram_user_id=6000)
    _enqueue(employee)
    assert len(api_client.get(OUTBOX, **bot_auth()).json()["messages"]) == 1


def test_rebinding_to_a_demo_chat_after_claim_cannot_deliver_on_retry(
    api_client, linked, employee
):
    """Строка ушла в RUNNING, потом привязку перевели в демо-диапазон.

    Уборка возвращает строку, и следующий захват снимает её — адрес
    берётся заново на каждый захват.
    """
    row = _enqueue(employee)
    outbox.claim()
    TelegramAccount.objects.filter(employee=employee).update(telegram_chat_id=5001)
    _age_lock(row.id)
    outbox.reclaim_stale()

    assert outbox.claim(now=timezone.now() + timedelta(hours=1)) == []
    assert Notification.objects.get(id=row.id).status == "CANCELLED"


# =====================================================================
# CRM /notifications: чужие строки и мусор во входе
# =====================================================================

@pytest.fixture()
def foreign_row(db, other_organization, foreign_office) -> Notification:
    person = Employee.objects.create(
        organization=other_organization, employee_number="FOR-0001",
        first_name="Пётр", last_name="Чужой", hire_date=date(2024, 1, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=other_organization, employee=person, office=foreign_office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 1, 1),
    )
    return Notification.objects.create(
        organization=other_organization, employee=person, channel="TELEGRAM",
        notification_type="absence.created", body="Чужое", status="FAILED",
    )


@pytest.fixture()
def hr_client(api_client, make_user, organization):
    user = make_user(
        organization,
        permissions=("notifications.read", "notifications.manage",
                     "employees.read", "audit.read"),
    )
    api_client.force_authenticate(user=user)
    return api_client


def test_foreign_organization_notification_is_invisible(hr_client, foreign_row):
    base = f"{API}/notifications/{foreign_row.id}"
    assert hr_client.get(f"{base}/").status_code == 404
    assert hr_client.get(f"{base}/attempts/").status_code == 404
    assert hr_client.post(f"{base}/retry/").status_code == 404
    assert hr_client.post(f"{base}/cancel/").status_code == 404
    listed = hr_client.get(f"{API}/notifications/").json()["items"]
    assert str(foreign_row.id) not in {row["id"] for row in listed}

    hr_client.post(f"{API}/notifications/retry-failed/", {}, format="json")
    assert Notification.objects.get(id=foreign_row.id).status == "FAILED"


def test_other_office_notification_is_invisible_to_office_scoped_user(
    api_client, make_user, organization, office, other_office, employee
):
    stranger = Employee.objects.create(
        organization=organization, employee_number="EMP-0999",
        first_name="Анна", last_name="Другая", hire_date=date(2024, 1, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=stranger, office=other_office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 1, 1),
    )
    row = Notification.objects.create(
        organization=organization, employee=stranger, channel="TELEGRAM",
        notification_type="absence.created", body="Не ваше", status="FAILED",
    )
    user = make_user(
        organization, office=office,
        permissions=("notifications.read", "notifications.manage",
                     "employees.read"),
    )
    api_client.force_authenticate(user=user)

    assert api_client.get(f"{API}/notifications/{row.id}/").status_code in (403, 404)
    assert api_client.post(
        f"{API}/notifications/{row.id}/retry/"
    ).status_code in (403, 404)
    assert api_client.post(
        f"{API}/notifications/retry-failed/", {}, format="json"
    ).json()["requeued"] == 0
    assert Notification.objects.get(id=row.id).status == "FAILED"


def test_without_permission_the_queue_is_closed(api_client, make_user, organization):
    user = make_user(organization, permissions=("employees.read",))
    api_client.force_authenticate(user=user)
    assert api_client.get(f"{API}/notifications/").status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        {"search": "a\x00b"},
        {"notification_type": "a\x00"},
        {"search": "x" * 20_000},
        {"search": "' OR 1=1 --"},
        {"search": "%_%"},
        {"status": "SENT,DROP TABLE"},
        {"channel": "SMS\x00"},
        {"employee_id": "не-uuid"},
        {"date_from": "2026-13-45"},
        {"limit": "abc"},
        {"limit": "-5"},
        {"limit": "99999999999999999999"},
        {"cursor": "!!!"},
    ],
)
def test_crm_list_rejects_garbage_without_500(hr_client, query):
    response = hr_client.get(f"{API}/notifications/", query)
    assert response.status_code in (200, 400), response.content[:300]
    counts = hr_client.get(f"{API}/notifications/counts/", query)
    assert counts.status_code in (200, 400), counts.content[:300]


def test_crm_retry_failed_type_prefix_with_nul_is_not_500(hr_client):
    response = hr_client.post(
        f"{API}/notifications/retry-failed/",
        {"notification_type": "absence.\x00"}, format="json",
    )
    assert response.status_code in (200, 400)


def test_crm_bad_pk_is_not_500(hr_client):
    assert hr_client.get(f"{API}/notifications/not-a-uuid/").status_code in (400, 404)


# =====================================================================
# /me/notifications и лента кадровика
# =====================================================================

@pytest.mark.parametrize("limit", ["abc", "1e3", "²", "-1", "9" * 30])
def test_employee_feed_limit_garbage_is_not_500(bot_client, linked, limit):
    response = bot_client.get(
        f"{API}/me/notifications", {"limit": limit}, **bot_headers()
    )
    assert response.status_code in (200, 400)


def test_employee_cannot_read_someone_elses_notification(
    bot_client, linked, employee, foreign_row, organization
):
    neighbour = Employee.objects.create(
        organization=organization, employee_number="EMP-0002",
        first_name="Олег", last_name="Сосед", hire_date=date(2024, 1, 1),
        employment_status="ACTIVE",
    )
    own_org = Notification.objects.create(
        organization=organization, employee=neighbour, channel="TELEGRAM",
        notification_type="absence.created", body="Соседу", status="SENT",
    )
    for row in (own_org, foreign_row):
        response = bot_client.post(
            f"{API}/me/notifications/{row.id}/read", **bot_headers()
        )
        assert response.status_code == 404
        row.refresh_from_db()
        assert row.read_at is None
    listed = bot_client.get(f"{API}/me/notifications", **bot_headers()).json()
    assert listed["items"] == []


@pytest.mark.parametrize("limit", ["²", "abc", "-1", "9" * 30])
def test_hr_feed_limit_garbage_is_not_500(hr_client, limit):
    response = hr_client.get(f"{API}/notification-feed", {"limit": limit})
    assert response.status_code in (200, 400)


@pytest.mark.parametrize(
    "event_id", ["absence_request:" + "x", "nope:" + str(uuid.uuid4()), "a" * 5000]
)
def test_hr_feed_bad_event_id_is_400(hr_client, event_id):
    assert hr_client.get(
        f"{API}/notification-feed/{event_id}"
    ).status_code == 400
    assert hr_client.post(
        f"{API}/notification-feed/{event_id}"
    ).status_code == 400


# =====================================================================
# Журнал аудита: неизменяемость, маскирование, фильтры
# =====================================================================

@pytest.fixture()
def audit_row(db, organization) -> AuditLog:
    return AuditLog.objects.create(
        organization=organization, action="test.action",
        entity_type="employees", entity_id=uuid.uuid4(),
        new_values={"status": "ACTIVE"},
    )


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_audit_api_has_no_write_methods(hr_client, audit_row, method):
    response = getattr(hr_client, method)(
        f"{API}/audit-logs", {"id": str(audit_row.id)}, format="json"
    )
    assert response.status_code == 405
    assert AuditLog.objects.filter(id=audit_row.id).exists()


@pytest.mark.parametrize("suffix", [f"/{uuid.uuid4()}", f"/{uuid.uuid4()}/"])
def test_audit_api_has_no_item_route(hr_client, suffix):
    assert hr_client.delete(f"{API}/audit-logs{suffix}").status_code in (404, 405)


def test_audit_row_cannot_be_changed_or_deleted_through_the_model(audit_row):
    audit_row.action = "forged"
    with pytest.raises(AuditLogImmutable):
        audit_row.save()
    with pytest.raises(AuditLogImmutable):
        audit_row.delete()
    assert AuditLog.objects.get(id=audit_row.id).action == "test.action"


def test_audit_requires_audit_read(api_client, make_user, organization, audit_row):
    user = make_user(organization, permissions=("employees.read",))
    api_client.force_authenticate(user=user)
    assert api_client.get(f"{API}/audit-logs").status_code == 403


def test_audit_does_not_show_another_organization(
    hr_client, other_organization
):
    foreign = AuditLog.objects.create(
        organization=other_organization, action="secret.action",
        entity_type="employees", entity_id=uuid.uuid4(),
    )
    ids = {
        row["id"] for row in hr_client.get(f"{API}/audit-logs").json()["items"]
    }
    assert str(foreign.id) not in ids
    assert hr_client.get(
        f"{API}/audit-logs", {"entity_id": str(foreign.entity_id)}
    ).json()["items"] == []


@pytest.mark.parametrize(
    "query",
    [
        {"action": "a\x00"},
        {"entity_type": "x\x00"},
        {"action": "x" * 20_000},
        {"entity_id": "' OR 1=1 --"},
        {"date_from": "2026-02-30"},
        {"limit": "²"},
        {"cursor": "e30="},
    ],
)
def test_audit_filters_reject_garbage_without_500(hr_client, query):
    response = hr_client.get(f"{API}/audit-logs", query)
    assert response.status_code in (200, 400), response.content[:300]


def test_audit_masks_nested_and_variant_secret_keys(organization):
    row = AuditLog.objects.create(
        organization=organization, action="test.secrets",
        entity_type="users", entity_id=uuid.uuid4(),
        new_values={
            "email": "hr@example.test",
            "new_password": "hunter2-fake",
            "Password": "fake",
            "settings": {"telegram_bot_token": FAKE_BOT_TOKEN, "name": "ok"},
            "items": [{"api_key": "sk-fake"}, {"qr_token_ttl_seconds": 30}],
            "note": f"token={FAKE_BOT_TOKEN}",
            "token_version": 3,
            "input_tokens": 10,
        },
        old_values={"authorization": "Bearer fake", "cookie": "sessionid=fake"},
    )
    row.refresh_from_db()
    text = str(row.new_values) + str(row.old_values)
    for leak in ("hunter2-fake", FAKE_BOT_TOKEN, "sk-fake", "Bearer fake",
                 "sessionid=fake"):
        assert leak not in text
    assert row.new_values["email"] == "hr@example.test"
    assert row.new_values["settings"]["name"] == "ok"
    assert row.new_values["token_version"] == 3
    assert row.new_values["input_tokens"] == 10
    assert row.new_values["items"][1] == {"qr_token_ttl_seconds": 30}


def test_redaction_helpers():
    assert redact_values(None) is None
    assert redact_values({"password": "x"}) == {"password": MASK}
    assert FAKE_BOT_TOKEN not in redact_text(f"url /bot{FAKE_BOT_TOKEN}/send")
    assert "fake-secret" not in redact_text("password=fake-secret")
    assert "abc.def" not in redact_text("Authorization: Bearer abc.def")
    assert redact_text("обычный текст 12:30") == "обычный текст 12:30"


def test_log_records_are_redacted():
    record = logging.LogRecord(
        "humotech", logging.WARNING, __file__, 1,
        "send failed %s password=%s", (f"bot{FAKE_BOT_TOKEN}", "fake-pass"), None,
    )
    assert SecretRedactingFilter().filter(record) is True
    message = record.getMessage()
    assert FAKE_BOT_TOKEN not in message
    assert "fake-pass" not in message


def test_logging_config_installs_the_redaction_filter(settings):
    handler = settings.LOGGING["handlers"]["console"]
    assert "redact_secrets" in handler.get("filters", [])
    root = logging.getLogger()
    assert any(
        isinstance(f, SecretRedactingFilter)
        for h in root.handlers for f in h.filters
    )


# =====================================================================
# События входа: функция для зоны auth
# =====================================================================

def test_login_events_are_recorded_without_secrets(make_user, organization):
    from humotech.audit.events import record_login

    user = make_user(organization, permissions=())
    row = record_login(
        user=user, success=False, reason="bad_password",
        ip_address="203.0.113.7", user_agent="UA" * 1000,
    )
    assert row.action == "auth.login.failed"
    assert row.entity_type == "users"
    assert row.entity_id == user.id
    assert row.organization_id == organization.id
    assert row.new_values == {"reason": "bad_password"}
    assert len(row.user_agent) <= 1000

    ok = record_login(user=user, success=True, ip_address="bad ip")
    assert ok.action == "auth.login.succeeded"
    assert ok.ip_address is None
    # Без пользователя (неизвестный email) записи в журнал нет:
    # организации у такой попытки нет.
    assert record_login(user=None, success=False, reason="unknown") is None
