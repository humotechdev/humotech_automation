"""Одна ссылка — одна привязка, даже когда по ней перешли одновременно.

Это единственный тест набора, которому нужны НАСТОЯЩИЕ параллельные
транзакции, поэтому он помечен `transaction=True`. Обычный `django_db`
оборачивает тест в одну транзакцию: два потока в ней не видят записей друг
друга, `select_for_update` блокируется на пустом месте, и тест проходит,
ничего не проверив.

Плата за это — таблицы очищаются усечением, а не откатом, и тест заметно
медленнее остальных. Он один такой, и он того стоит: без него защита от
гонки существует только на словах.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation
from humotech.telegram.services import TelegramLinkError, TelegramLinkService

TELEGRAM_FULL = ("employees.read", "telegram.read", "telegram.manage")


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_clicks_create_one_binding(
    make_actor, organization, employee, telegram_settings
):
    """Второй перешедший получает отказ, а не вторую привязку.

    Сценарий не выдуманный: ссылку переслали, и по ней постучались с двух
    устройств почти одновременно.
    """
    hr = make_actor(organization, permissions=TELEGRAM_FULL)
    issued = TelegramLinkService().create_invitation(hr, employee.id)

    # Оба потока стартуют по одному сигналу: иначе первый успевает
    # закончить раньше, чем второй начнёт, и гонки не происходит вовсе.
    ready = threading.Barrier(2)

    def click(telegram_user_id: int):
        ready.wait(timeout=10)
        try:
            TelegramLinkService().consume(
                token=issued.token,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_user_id,
            )
            return "ok"
        except TelegramLinkError as exc:
            return exc.reason
        finally:
            # Каждый поток работает со своим соединением, и оставлять его
            # открытым нельзя: усечение таблиц в конце теста повиснет.
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(
            pool.map(click, [811_000, 812_000]), key=lambda item: item != "ok"
        )

    assert results[0] == "ok", f"ни один переход не удался: {results}"
    assert results[1] != "ok", f"привязались оба: {results}"

    assert TelegramAccount.objects.filter(employee=employee).count() == 1
    account = TelegramAccount.objects.get(employee=employee)
    assert account.status == "PENDING"
    assert account.telegram_user_id in (811_000, 812_000)

    invitation = TelegramLinkInvitation.objects.get(id=issued.invitation.id)
    assert invitation.status == "PENDING_CONFIRMATION"
    # Погашение записано за тем, кто успел первым, — и только за ним.
    assert invitation.consumed_by_telegram_user_id == account.telegram_user_id
