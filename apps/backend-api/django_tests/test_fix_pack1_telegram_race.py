"""A-25: подтверждение и отключение привязки Telegram не затирают друг друга.

Нужны НАСТОЯЩИЕ параллельные транзакции (`transaction=True`), как в
`test_telegram_concurrency.py`. Поток подтверждения останавливается сразу
после проверок — в этот момент второй поток отключает привязку. Без
блокировки подтверждение потом сохраняло ACTIVE поверх REVOKED, и
отключённая привязка «оживала» (аудит 12.10, A-25).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

from django_tests.conftest import link_telegram
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation
from humotech.telegram.services import TelegramLinkService

TELEGRAM_FULL = ("employees.read", "telegram.read", "telegram.manage")


@pytest.mark.django_db(transaction=True)
def test_confirm_does_not_resurrect_concurrently_disconnected_link(
    make_actor, organization, employee, telegram_settings, monkeypatch
):
    hr = make_actor(organization, permissions=TELEGRAM_FULL)
    issued = TelegramLinkService().create_invitation(hr, employee.id)
    TelegramLinkService().consume(token=issued.token, telegram_user_id=5_101,
                                  telegram_chat_id=5_101)
    invitation = TelegramLinkInvitation.objects.get(employee=employee,
                                                    status="PENDING_CONFIRMATION")

    paused = threading.Event()
    original_now = TelegramLinkService._now

    def slow_now(self):
        # Только поток подтверждения задерживается — после проверок, перед записью.
        if threading.current_thread().name.startswith("confirm"):
            paused.set()
            threading.Event().wait(1.5)
        return original_now(self)

    monkeypatch.setattr(TelegramLinkService, "_now", slow_now)

    def confirm():
        try:
            TelegramLinkService().confirm(hr, invitation.id)
            return "confirmed"
        except Exception as error:  # noqa: BLE001 — исход сравнивается ниже
            return type(error).__name__
        finally:
            connection.close()

    def disconnect():
        paused.wait(timeout=10)
        try:
            TelegramLinkService().disconnect(hr, employee.id)
            return "disconnected"
        except Exception as error:  # noqa: BLE001
            return type(error).__name__
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="w") as pool:
        first = pool.submit(lambda: _named("confirm", confirm))
        second = pool.submit(lambda: _named("disconnect", disconnect))
        outcomes = {first.result(timeout=30), second.result(timeout=30)}

    account = TelegramAccount.objects.get(employee=employee)
    # Отключение — последнее по времени действие: привязка обязана остаться
    # отключённой, какой бы исход ни получило подтверждение.
    assert account.status == "REVOKED", (account.status, outcomes)
    link_telegram  # импорт помощника — для единообразия с соседними тестами


def _named(name, fn):
    threading.current_thread().name = name
    return fn()


@pytest.mark.django_db(transaction=True)
def test_accept_terms_and_confirm_take_locks_in_one_order(
    make_actor, organization, employee, telegram_settings, monkeypatch
):
    """Согласие сотрудника и подтверждение HR в одну секунду — без deadlock.

    `confirm` блокирует приглашения, затем привязку. Если `accept_terms`
    берёт их в обратном порядке, Postgres обрывает одну из транзакций
    (DeadlockDetected → 500 у HR или у сотрудника).
    """
    from django.db.models.query import QuerySet

    from humotech.telegram.services import TelegramLinkError

    hr = make_actor(organization, permissions=TELEGRAM_FULL)
    issued = TelegramLinkService().create_invitation(hr, employee.id)
    TelegramLinkService().consume(token=issued.token, telegram_user_id=5_102,
                                  telegram_chat_id=5_102)
    invitation = TelegramLinkInvitation.objects.get(employee=employee,
                                                    status="PENDING_CONFIRMATION")

    paused = threading.Event()
    original_first = QuerySet.first

    def slow_first(self):
        result = original_first(self)
        # Поток согласия задерживается сразу после блокировки привязки.
        if (threading.current_thread().name == "accept"
                and self.model is TelegramAccount and self.query.select_for_update):
            paused.set()
            threading.Event().wait(1.5)
        return result

    monkeypatch.setattr(QuerySet, "first", slow_first)

    def accept():
        try:
            TelegramLinkService().accept_terms(telegram_user_id=5_102)
            return "accepted"
        except TelegramLinkError as error:
            return error.reason
        except Exception as error:  # noqa: BLE001
            return type(error).__name__
        finally:
            connection.close()

    def confirm():
        paused.wait(timeout=10)
        try:
            TelegramLinkService().confirm(hr, invitation.id)
            return "confirmed"
        except Exception as error:  # noqa: BLE001
            return type(error).__name__
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lambda: _named("accept", accept))
        second = pool.submit(lambda: _named("confirm", confirm))
        outcomes = (first.result(timeout=30), second.result(timeout=30))

    assert paused.is_set(), "контроль: поток согласия не дошёл до блокировки"
    assert outcomes == ("hr_confirmation_required", "confirmed"), outcomes
