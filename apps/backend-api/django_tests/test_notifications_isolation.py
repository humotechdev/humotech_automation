"""Предохранители против синтетических уведомлений в рабочей базе.

Проверяется не «команда умеет заводить строки», а то, что она ОТКАЗЫВАЕТСЯ
их заводить всюду, кроме изолированного стенда, — и отказывается ДО того,
как что-либо записала. Каждая из трёх причин отказа проверяется отдельно:
защита, которая держится только на всех трёх сразу, держится ни на чём.
"""

from __future__ import annotations

import socket
from datetime import date

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from humotech.employees.models import Employee
from humotech.notifications.isolation import (
    E2E_CHAT_ID_FLOOR,
    ENV_MARKER,
    is_stand_chat_id,
    require_isolated_stand,
)
from humotech.notifications.models import Notification
from humotech.telegram.models import TelegramAccount

# Настоящий идентификатор чата: пятизначный и больше. Ровно такие стоят
# у живых привязок, и ровно их наличие делает базу опасной.
REAL_CHAT_ID = 6173198743


@pytest.fixture()
def stand(monkeypatch):
    """Окружение изолированного стенда: переменная и тестовое имя базы."""
    monkeypatch.setenv(ENV_MARKER, "1")
    # Имя тестовой базы и так начинается с `test_`, но полагаться на это
    # молча нельзя: проверка должна оставаться осмысленной, даже если
    # настройки тестов переименуют базу.
    monkeypatch.setitem(connection.settings_dict, "NAME", "test_humotech_e2e")


def binding(organization, employee, chat_id: int, status: str = "ACTIVE"):
    return TelegramAccount.objects.create(
        organization=organization,
        employee=employee,
        telegram_user_id=chat_id,
        telegram_chat_id=chat_id,
        status=status,
        connected_at=timezone.now(),
    )


class TestStandGuard:
    def test_без_переменной_окружения_отказ(self, db, monkeypatch):
        monkeypatch.delenv(ENV_MARKER, raising=False)
        with pytest.raises(CommandError, match=ENV_MARKER):
            require_isolated_stand()

    def test_рабочее_имя_базы_отказ(self, db, monkeypatch):
        monkeypatch.setenv(ENV_MARKER, "1")
        monkeypatch.setitem(
            connection.settings_dict, "NAME", "humotech_django"
        )
        with pytest.raises(CommandError, match="не похожа на тестовую"):
            require_isolated_stand()

    def test_живая_привязка_отказ(self, db, stand, organization, employee):
        binding(organization, employee, REAL_CHAT_ID)
        with pytest.raises(CommandError, match="настоящими идентификаторами"):
            require_isolated_stand()

    def test_отозванная_привязка_не_мешает(
        self, db, stand, organization, employee
    ):
        binding(organization, employee, REAL_CHAT_ID, status="REVOKED")
        require_isolated_stand()  # не должно бросить

    def test_стендовая_привязка_не_мешает(
        self, db, stand, organization, employee
    ):
        binding(organization, employee, E2E_CHAT_ID_FLOOR)
        require_isolated_stand()

    def test_настоящие_идентификаторы_не_попадают_в_диапазон_стенда(self):
        assert is_stand_chat_id(E2E_CHAT_ID_FLOOR)
        assert not is_stand_chat_id(REAL_CHAT_ID)
        # Отрицательные принадлежат группам и каналам — тоже настоящим.
        assert not is_stand_chat_id(-1001234567890)


class TestSeedCommand:
    def test_на_рабочей_базе_ничего_не_заводится(
        self, db, monkeypatch, organization, employee
    ):
        monkeypatch.setenv(ENV_MARKER, "1")
        monkeypatch.setitem(
            connection.settings_dict, "NAME", "humotech_django"
        )

        with pytest.raises(CommandError):
            call_command("seed_demo_notifications", "--count", "3")

        assert Notification.objects.count() == 0, (
            "отказ должен произойти ДО первой записи"
        )

    def test_живые_адресаты_останавливают_посев(
        self, db, stand, organization, employee
    ):
        binding(organization, employee, REAL_CHAT_ID)

        with pytest.raises(CommandError):
            call_command("seed_demo_notifications", "--count", "3")

        assert Notification.objects.count() == 0

    def test_на_изолированном_стенде_заводит(
        self, db, stand, organization, employee
    ):
        call_command("seed_demo_notifications", "--count", "4")

        rows = Notification.objects.all()
        assert rows.count() == 4
        # Все заведены как строки с полной историей: они моложе таблицы.
        assert all(row.attempt_history_complete for row in rows)

    def test_адресаты_стенда_из_безопасного_диапазона(
        self, db, stand, organization, employee
    ):
        call_command(
            "seed_demo_notifications", "--count", "2", "--with-recipients"
        )

        chats = list(
            TelegramAccount.objects.values_list("telegram_chat_id", flat=True)
        )
        assert chats and all(is_stand_chat_id(chat) for chat in chats)


class TestNetworkGuard:
    def test_выход_наружу_проваливает_тест_немедленно(self):
        """Предохранитель из conftest: тест не может дойти до Telegram."""
        with pytest.raises(RuntimeError, match="Наружу из тестов"):
            socket.socket().connect(("149.154.167.220", 443))

    def test_база_остаётся_доступной(self, db, organization):
        """Предохранитель не должен перекрыть собственное подключение."""
        assert Employee.objects.filter(organization=organization).count() >= 0
        assert date.today() is not None
