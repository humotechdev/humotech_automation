"""Разбор удалённых уведомлений: что пропало и на каких условиях вернуть.

Проверяется главное свойство команды: она не превращает «мы не знаем» в
запись. Набор пропавших строк она устанавливает точно — по журналу и
событиям, — а всё, чего в источниках нет, оставляет неизвестным и требует
объявить явно.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from humotech.audit.models import AuditLog
from humotech.notifications.models import Notification
from humotech.telegram.models import TelegramLinkInvitation

CONFIRMED_AT = datetime(2026, 9, 4, 9, 33, 13, 702521, tzinfo=UTC)


@pytest.fixture()
def confirmed_link(db, organization, employee, make_user):
    """Подтверждённая привязка: событие, за которым стояло уведомление."""
    invitation = TelegramLinkInvitation.objects.create(
        organization=organization,
        employee=employee,
        created_by_user=make_user(organization),
        token_hash="x" * 64,
        status="USED",
        expires_at=timezone.now(),
    )
    AuditLog.objects.create(
        organization=organization,
        action="telegram.invitation.confirm",
        entity_type="telegram_link_invitations",
        entity_id=invitation.id,
        occurred_at=CONFIRMED_AT,
    )
    return invitation


def key_of(invitation) -> str:
    return f"telegram-link:{invitation.id}:confirmed"


class TestInventory:
    def test_пропавшая_строка_находится_по_журналу(
        self, confirmed_link, capsys
    ):
        call_command("restore_deleted_notifications")

        printed = capsys.readouterr().out
        assert "Пропавших уведомлений: 1" in printed
        assert key_of(confirmed_link) in printed
        assert CONFIRMED_AT.isoformat() in printed

    def test_сухой_прогон_ничего_не_пишет(self, confirmed_link):
        call_command("restore_deleted_notifications")

        assert Notification.objects.count() == 0

    def test_уцелевшая_строка_пропавшей_не_считается(
        self, confirmed_link, organization, employee, capsys
    ):
        Notification.objects.create(
            organization=organization, employee=employee, channel="TELEGRAM",
            notification_type="telegram.link.confirmed", body="текст",
            status="SENT", idempotency_key=key_of(confirmed_link),
        )

        call_command("restore_deleted_notifications")

        assert "Пропавших уведомлений не найдено" in capsys.readouterr().out


class TestWriting:
    def test_без_объявленного_исхода_запись_отклоняется(self, confirmed_link):
        with pytest.raises(CommandError, match="без --outcome"):
            call_command("restore_deleted_notifications", "--apply")

        assert Notification.objects.count() == 0

    def test_в_очередь_восстановленное_не_попадает(self, confirmed_link):
        """PENDING среди допустимых исходов нет: возврат не отправляет."""
        with pytest.raises(CommandError):
            call_command(
                "restore_deleted_notifications", "--apply",
                "--outcome", "PENDING",
            )

        assert Notification.objects.count() == 0

    def test_запись_помечает_неизвестное_неизвестным(self, confirmed_link):
        call_command(
            "restore_deleted_notifications", "--apply", "--outcome", "SENT"
        )

        row = Notification.objects.get()
        assert row.status == "SENT"
        # Время отправки не выдумано даже там, где исход объявлен.
        assert row.sent_at is None
        assert row.attempts == 0
        # История этой строки не велась и не появится.
        assert row.attempt_history_complete is False
        assert row.created_at == CONFIRMED_AT
        assert "не сохранился" in row.body

    def test_повторный_запуск_не_плодит_дубликатов(self, confirmed_link):
        for _ in range(3):
            call_command(
                "restore_deleted_notifications", "--apply", "--outcome", "SENT"
            )

        assert Notification.objects.count() == 1


class TestUnknownRecipient:
    def test_событие_без_адресата_пропускается_а_не_угадывается(
        self, db, organization, capsys
    ):
        AuditLog.objects.create(
            organization=organization,
            action="absence.request.create",
            entity_type="absence_requests",
            # Заявки с таким идентификатором нет: адресата взять неоткуда.
            entity_id="00000000-0000-0000-0000-000000000001",
            occurred_at=timezone.now(),
        )

        call_command("restore_deleted_notifications")

        captured = capsys.readouterr()
        assert "адресат не определён" in captured.err
        assert Notification.objects.count() == 0
        assert date.today() is not None
