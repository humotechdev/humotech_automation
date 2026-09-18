"""Отправщик очереди: настоящий, заглушка и отказ их перепутать."""

from __future__ import annotations

import asyncio
import socket

import pytest

from src.notifications import sender as sender_module
from src.notifications.sender import (
    StubSender,
    StubSenderRefused,
    build_stub_sender,
)
from src.notifications.worker import tick


class FakeClient:
    """Backend, который отдаёт пачку и запоминает отчёт."""

    def __init__(self, messages):
        self.messages = messages
        self.reported = None

    async def claim_notifications(self):
        return {"messages": self.messages}

    async def report_notifications(self, results):
        self.reported = results


def message(number: int, kind: str = "absence.approved") -> dict:
    return {
        "id": f"id-{number}",
        "chat_id": 100 + number,
        "text": "проверочное сообщение",
        "type": kind,
        "attempts": 0,
    }


class TestStubSender:
    def test_доставка_проходит_очередь_целиком(self):
        client = FakeClient([message(1), message(2)])

        count = asyncio.run(tick(StubSender(), client))

        assert count == 2
        assert client.reported == [
            {"id": "id-1", "sent": True},
            {"id": "id-2", "sent": True},
        ]

    def test_названный_тип_объявляется_неудачным(self):
        client = FakeClient([message(1, "absence.approved"), message(2, "x.y")])

        asyncio.run(tick(StubSender(fail_types=("absence.",)), client))

        assert client.reported == [
            {"id": "id-1", "sent": False, "error": "stub_forced_failure"},
            {"id": "id-2", "sent": True},
        ]

    def test_заглушка_не_открывает_ни_одного_соединения(self):
        # Предохранитель conftest уже стоит; здесь проверяется, что путь
        # доставки его не задевает вовсе.
        client = FakeClient([message(1)])
        asyncio.run(tick(StubSender(), client))
        assert client.reported == [{"id": "id-1", "sent": True}]


class TestTokenRefusal:
    def test_заглушка_отказывается_работать_с_настоящим_токеном(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            sender_module.settings,
            "bot_token",
            "8836690896:AAF" + "x" * 32,
            raising=False,
        )
        with pytest.raises(StubSenderRefused):
            build_stub_sender()

    def test_подставной_токен_принимается(self, monkeypatch):
        monkeypatch.setattr(
            sender_module.settings, "bot_token", "stub", raising=False
        )
        monkeypatch.setattr(
            sender_module.settings,
            "notifications_stub_fail",
            "absence.",
            raising=False,
        )
        stub = build_stub_sender()
        assert isinstance(stub, StubSender)


class TestNetworkGuard:
    def test_попытка_выйти_наружу_проваливает_тест_немедленно(self):
        # Класс объявлен в conftest, который pytest импортирует как
        # модуль верхнего уровня, — поэтому ловим по сообщению, а не по
        # импортированному имени: оно было бы другим объектом.
        with pytest.raises(RuntimeError, match="наружу не ходят"):
            socket.socket().connect(("149.154.167.220", 443))


class TestSurveyButton:
    """Опрос уходит ОДНИМ сообщением с кнопкой, а не вопросами подряд.

    В чате нельзя вернуться на предыдущий вопрос, нельзя увидеть,
    сколько осталось, и нельзя ответить шкалой. Поэтому вопросы
    показывает Mini App, а в чат уходит короткое приглашение.
    """

    def setup_method(self):
        from src.config.settings import settings

        self._was = settings.mini_app_url
        settings.mini_app_url = "https://miniapp.example/app"

    def teardown_method(self):
        from src.config.settings import settings

        settings.mini_app_url = self._was

    def test_приглашение_несёт_кнопку_на_свой_опрос(self):
        from src.notifications.buttons import markup_for

        markup = markup_for("survey.invite", "11111111-2222-3333-4444-555555555555")

        assert markup is not None
        button = markup.inline_keyboard[0][0]
        assert button.text == "Пройти опрос"
        # Кнопка ведёт именно к этому опросу, а не «к опросам вообще».
        assert button.web_app.url.endswith(
            "/survey/11111111-2222-3333-4444-555555555555"
        )

    def test_у_обычного_уведомления_кнопки_нет(self):
        from src.notifications.buttons import markup_for

        assert markup_for("absence.approved", "some-id") is None

    def test_без_ссылки_на_опрос_кнопки_нет(self):
        # Кнопка открыла бы «какой-то» опрос — это хуже её отсутствия.
        from src.notifications.buttons import markup_for

        assert markup_for("survey.invite", None) is None

    def test_без_адреса_mini_app_кнопки_нет(self):
        from src.config.settings import settings
        from src.notifications.buttons import markup_for

        settings.mini_app_url = ""
        assert markup_for("survey.invite", "any-id") is None

    def test_идентификатор_экранируется(self):
        from src.notifications.buttons import survey_url

        url = survey_url("../../admin")
        assert url is not None
        assert "/survey/..%2F..%2Fadmin" in url

    def test_ссылка_доезжает_от_очереди_до_отправщика(self):
        """Идентификатор из очереди должен дойти до кнопки целиком."""
        seen = {}

        class Watching:
            async def deliver(self, *, chat_id, text, notification_type,
                              entity_id=None):
                seen["entity_id"] = entity_id
                seen["type"] = notification_type
                from src.notifications.sender import Outcome

                return Outcome(sent=True)

        item = message(1, "survey.invite")
        item["entity_id"] = "recipient-77"
        client = FakeClient([item])

        asyncio.run(tick(Watching(), client))

        assert seen == {"entity_id": "recipient-77", "type": "survey.invite"}
