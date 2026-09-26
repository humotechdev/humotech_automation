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
                              entity_id=None, attachment=None,
                              telegram_user_id=None):
                seen["entity_id"] = entity_id
                seen["type"] = notification_type
                seen["attachment"] = attachment
                from src.notifications.sender import Outcome

                return Outcome(sent=True)

        item = message(1, "survey.invite")
        item["entity_id"] = "recipient-77"
        client = FakeClient([item])

        asyncio.run(tick(Watching(), client))

        # Вложения у приглашения на опрос нет: файл прикладывается
        # только там, где он есть, — у заявления на больничный.
        assert seen == {
            "entity_id": "recipient-77",
            "type": "survey.invite",
            "attachment": None,
        }


class TestReplyFile:
    """Файл к ответу HR: документом, с подписью, и не молча без него."""

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_document(
            self, chat_id, document, caption=None, reply_markup=None, **kwargs
        ):
            # Очередь шлёт обычный текст: разметку отключают явно.
            assert "parse_mode" in kwargs and kwargs["parse_mode"] is None
            self.sent.append(("document", document.filename, caption))

        async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
            assert "parse_mode" in kwargs and kwargs["parse_mode"] is None
            self.sent.append(("message", None, text))

    class Files:
        def __init__(self, fail=False):
            self.fail = fail

        async def question_reply_file(self, telegram_id, message_id):
            if self.fail:
                from src.api.errors import ApiError

                raise ApiError(404, "not_found", "нет")
            return b"%PDF", "Бланк.pdf"

    def deliver(self, bot, files, text):
        from src.notifications.sender import TelegramSender

        return asyncio.run(TelegramSender(bot, files).deliver(
            chat_id=1, text=text, notification_type="question.reply.file",
            entity_id="6f1b1c1e-0000-4000-8000-000000000001",
            attachment="question_file", telegram_user_id=7,
        ))

    def test_короткий_ответ_уходит_подписью_к_файлу(self):
        bot = self.Bot()

        outcome = self.deliver(bot, self.Files(), "💬 Ответ HR по обращению №5\n\nВот бланк")

        assert outcome.sent is True
        assert bot.sent == [("document", "Бланк.pdf", "💬 Ответ HR по обращению №5\n\nВот бланк")]

    def test_длинный_ответ_не_теряется_из_за_предела_подписи(self):
        bot = self.Bot()
        text = "💬 Ответ HR по обращению №5\n\n" + "а" * 2000

        self.deliver(bot, self.Files(), text)

        # Текст целиком — сообщением, файл — следом с первой строкой: по
        # ней бот узнаёт ответ HR, если человек ответит прямо на файл.
        assert bot.sent == [
            ("message", None, text),
            ("document", "Бланк.pdf", "💬 Ответ HR по обращению №5"),
        ]

    def test_без_файла_ответ_не_считается_доставленным(self):
        bot = self.Bot()

        outcome = self.deliver(bot, self.Files(fail=True), "💬 Ответ HR по обращению №5")

        assert outcome.sent is False
        assert bot.sent == []
