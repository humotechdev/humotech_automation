"""Очередь уведомлений: транзакционность, повторы, адресат.

Три свойства, ради которых очередь вообще заведена:

  1. уведомление и событие живут или умирают вместе — иначе человек
     получает «отпуск подтверждён» про отпуск, которого нет;
  2. одно событие даёт одно сообщение, сколько бы раз обработчик
     ни сработал;
  3. сообщение не уходит постороннему. Чат берётся из привязки того же
     сотрудника, а не из строки уведомления, — дойти до чужого неоткуда.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from humotech.absences.models import AbsenceType, LeaveBalance
from humotech.absences.services import MINUTES_PER_WORKING_DAY, AbsenceService
from humotech.notifications import outbox
from humotech.notifications.models import Notification
from humotech.telegram.identity import resolve_by_telegram_user_id
from humotech.telegram.models import TelegramAccount

from .conftest import TEST_BOT_SECRET, link_telegram

pytestmark = pytest.mark.django_db

TG_ID = 777_000_111
OUTBOX = "/api/v1/telegram/bot/outbox"


@pytest.fixture()
def context(db, employee, telegram_settings):
    link_telegram(employee)
    return resolve_by_telegram_user_id(TG_ID)


@pytest.fixture()
def sick_leave(db, organization) -> AbsenceType:
    return AbsenceType.objects.create(
        organization=organization, code="SICK_LEAVE", name="Больничный",
        requires_approval=True,
    )


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


def bot_auth() -> dict:
    return {"HTTP_X_BOT_TOKEN": TEST_BOT_SECRET}


def soon(days: int) -> date:
    return date.today() + timedelta(days=days)


# --- транзакционность ------------------------------------------------------

def test_request_and_notification_are_created_together(
    context, sick_leave
):
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )

    row = Notification.objects.get(notification_type="absence.created")
    assert row.status == "PENDING"
    assert row.channel == "TELEGRAM"


def test_rolled_back_request_leaves_no_notification(context, sick_leave):
    """Откатилась заявка — откатилось и уведомление.

    Отдельная транзакция для уведомления означала бы, что человек получит
    сообщение о том, чего не произошло.
    """
    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with transaction.atomic():
            AbsenceService().create(
                context, absence_type_code="SICK_LEAVE",
                first_day=soon(0), last_day=soon(2),
            )
            raise Boom

    assert not Notification.objects.exists()


def test_the_same_event_gives_one_message(context, sick_leave):
    """Ключ повтора — свойство схемы, а не аккуратности вызывающего."""
    outbox.enqueue(
        organization_id=context.organization_id,
        employee_id=context.employee.id,
        notification_type="absence.created",
        body="Заявка отправлена",
        idempotency_key="повтор",
    )
    outbox.enqueue(
        organization_id=context.organization_id,
        employee_id=context.employee.id,
        notification_type="absence.created",
        body="Заявка отправлена",
        idempotency_key="повтор",
    )

    assert Notification.objects.count() == 1


# --- содержимое ------------------------------------------------------------

def test_the_message_carries_no_diagnosis(context, sick_leave):
    """Комментарий сотрудника в уведомление не попадает.

    В нём может быть что угодно, включая диагноз, а сообщение уходит
    в чат рядом с перепиской с родственниками.
    """
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
        comment="Ангина, температура 39, назначен антибиотик",
    )

    row = Notification.objects.get(notification_type="absence.created")
    assert "Ангина" not in row.body
    assert "антибиотик" not in row.body
    assert "больничный" in row.body.lower()


def test_unknown_absence_type_gets_a_neutral_wording(context, organization):
    """«Отпуск» в чате про декрет выглядел бы небрежно."""
    AbsenceType.objects.create(
        organization=organization, code="MATERNITY", name="Декретный отпуск",
        requires_approval=True,
    )
    AbsenceService().create(
        context, absence_type_code="MATERNITY",
        first_day=soon(0), last_day=soon(30),
    )

    row = Notification.objects.get(notification_type="absence.created")
    assert "отсутствие" in row.body.lower()


# --- захват и отправка -----------------------------------------------------

def test_claim_returns_the_chat_from_the_current_binding(
    context, sick_leave, notification_settings
):
    """Чат берётся из привязки, а не из строки уведомления.

    Поэтому подменить адресата нечем: в самом уведомлении его нет.
    """
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )

    batch = outbox.claim()

    assert len(batch) == 1
    assert batch[0].chat_id == TG_ID
    assert Notification.objects.get(id=batch[0].id).status == "RUNNING"


def test_a_claimed_message_is_not_handed_out_twice(
    context, sick_leave, notification_settings
):
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )

    assert len(outbox.claim()) == 1
    assert outbox.claim() == []


def test_revoked_binding_cancels_the_message(
    context, sick_leave, notification_settings
):
    """Уведомление о заявке не уходит никому, если привязки больше нет.

    Не ошибка отправки: повторять нечего, и держать строку в очереди
    вечно незачем. Причина сохраняется — иначе непонятно, почему человек
    ничего не получил.
    """
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    TelegramAccount.objects.filter(employee=context.employee).update(
        status="REVOKED"
    )

    assert outbox.claim() == []
    row = Notification.objects.get(notification_type="absence.created")
    assert row.status == "CANCELLED"
    assert row.error_message == "link_revoked"


def test_rejection_of_the_binding_still_reaches_that_person(
    db, employee, telegram_settings, hr_actor, make_actor, organization,
    notification_settings,
):
    """Сообщение об отказе адресовано именно тому, кто открывал ссылку.

    К моменту отправки его привязка уже отозвана, и общее правило её бы
    отбросило. Исключение объявлено по типу уведомления, а чат всё равно
    берётся из строки привязки ТОГО ЖЕ сотрудника — дойти до постороннего
    сообщению неоткуда.
    """
    from humotech.telegram.services import TelegramLinkService

    actor = make_actor(organization, permissions=("telegram.read",
                                                  "telegram.manage",
                                                  "employees.read"))
    service = TelegramLinkService()
    issued = service.create_invitation(actor, employee.id)
    service.consume(
        token=issued.token, telegram_user_id=TG_ID, telegram_chat_id=TG_ID
    )
    service.reject(actor, issued.invitation.id)

    batch = outbox.claim()

    assert len(batch) == 1
    assert batch[0].notification_type == "telegram.link.rejected"
    assert batch[0].chat_id == TG_ID


# --- повторы ---------------------------------------------------------------

def test_a_failure_returns_the_message_with_a_pause(
    context, sick_leave, notification_settings
):
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    item = outbox.claim()[0]

    outbox.mark_failed(item.id, error="Bad Gateway")

    row = Notification.objects.get(id=item.id)
    assert row.status == "PENDING"
    assert row.attempts == 1
    assert row.next_attempt_at > timezone.now()
    # Раньше паузы сообщение не выдаётся.
    assert outbox.claim() == []


def test_the_pause_grows_with_each_attempt(
    context, sick_leave, notification_settings
):
    """Сервер, ответивший ошибкой, редко чинится за секунду.

    Долбить его каждую секунду — верный способ получить ограничение
    уже за поведение, а не за первую ошибку.
    """
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    pauses = []
    for _ in range(2):
        item = outbox.claim(now=timezone.now() + timedelta(hours=1))[0]
        moment = timezone.now()
        outbox.mark_failed(item.id, error="timeout", now=moment)
        row = Notification.objects.get(id=item.id)
        pauses.append(row.next_attempt_at - moment)

    assert pauses[1] > pauses[0]


def test_attempts_run_out_and_the_message_fails(
    context, sick_leave, notification_settings
):
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    later = timezone.now()
    for _ in range(notification_settings["MAX_ATTEMPTS"]):
        later += timedelta(hours=2)
        item = outbox.claim(now=later)[0]
        outbox.mark_failed(item.id, error="timeout", now=later)

    row = Notification.objects.get(notification_type="absence.created")
    assert row.status == "FAILED"
    assert row.attempts == notification_settings["MAX_ATTEMPTS"]
    assert outbox.claim(now=later + timedelta(days=1)) == []


def test_success_closes_the_message(context, sick_leave, notification_settings):
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    item = outbox.claim()[0]

    outbox.mark_sent(item.id)

    row = Notification.objects.get(id=item.id)
    assert row.status == "SENT"
    assert row.sent_at is not None
    assert row.locked_at is None


def test_a_stuck_message_is_reclaimed(context, sick_leave, notification_settings):
    """Процесс отправщика мог упасть между захватом и результатом.

    Блокировка строки снимется сама, статус RUNNING — нет; без уборки
    такое сообщение не ушло бы никогда.
    """
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    item = outbox.claim()[0]
    Notification.objects.filter(id=item.id).update(
        locked_at=timezone.now() - timedelta(hours=1)
    )

    assert outbox.reclaim_stale() == 1
    assert Notification.objects.get(id=item.id).status == "PENDING"
    assert len(outbox.claim()) == 1


def test_the_error_text_is_truncated(context, sick_leave, notification_settings):
    """Ответ Telegram может содержать эхо запроса — то есть само уведомление."""
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    item = outbox.claim()[0]

    outbox.mark_failed(item.id, error="э" * 5000)

    assert len(Notification.objects.get(id=item.id).error_message) <= 200


# --- через API бота --------------------------------------------------------

def test_bot_fetches_and_reports(api_client, context, sick_leave,
                                 notification_settings):
    AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )

    body = api_client.get(OUTBOX, **bot_auth()).json()
    assert len(body["messages"]) == 1
    message_id = body["messages"][0]["id"]

    reported = api_client.post(
        OUTBOX, {"results": [{"id": message_id, "sent": True}]},
        format="json", **bot_auth(),
    )

    assert reported.json()["accepted"] == 1
    assert Notification.objects.get(id=message_id).status == "SENT"


def test_outbox_is_closed_without_the_bot_secret(api_client, context,
                                                 sick_leave):
    assert api_client.get(OUTBOX).status_code == 403
    assert api_client.get(
        OUTBOX, HTTP_X_BOT_TOKEN="не тот секрет"
    ).status_code == 403


def test_full_circle_from_approval_to_the_queue(
    context, sick_leave, make_actor, organization, notification_settings
):
    """Подтвердил кадровик — сообщение готово к отправке."""
    actor = make_actor(
        organization,
        permissions=("employees.read", "absences.read", "absences.approve"),
    )
    service = AbsenceService()
    view = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(0), last_day=soon(2),
    )
    Notification.objects.all().delete()

    service.decide(actor, view.request.id, approve=True)

    batch = outbox.claim()
    assert len(batch) == 1
    assert "подтверждён" in batch[0].text.lower()
