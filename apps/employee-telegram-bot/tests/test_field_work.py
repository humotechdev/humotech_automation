"""Запрос выездной работы: кнопки, проверка Telegram identity и итог ответа."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.handlers.field_work.router import decide
from src.notifications.buttons import (
    FIELD_WORK_CONFIRM, FIELD_WORK_DECLINE, FIELD_WORK_REQUEST, markup_for,
)
from src.notifications.sender import TelegramSender

REQUEST_ID = "43f6ff78-628d-435d-9f00-50fae2acc555"


def run(coro):
    return asyncio.run(coro)


class Message:
    def __init__(self, *, chat_id=123, chat_type="private"):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.answers = []
        self.edits = 0

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_reply_markup(self, **kwargs):
        self.edits += 1


class Call:
    def __init__(self, data, message=None):
        self.data = data
        self.from_user = SimpleNamespace(id=123)
        self.message = message or Message()
        self.acks = []

    async def answer(self, *args, **kwargs):
        self.acks.append((args, kwargs))


class Client:
    def __init__(self, status="CONFIRMED", error=None):
        self.status = status
        self.error = error
        self.calls = []

    async def field_work_decision(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"status": self.status, "id": REQUEST_ID, "date": "2026-09-28"}


def test_markup_contains_only_uuid_and_two_choices():
    markup = markup_for(FIELD_WORK_REQUEST, REQUEST_ID)
    assert [button.callback_data for row in markup.inline_keyboard for button in row] == [
        FIELD_WORK_CONFIRM + REQUEST_ID, FIELD_WORK_DECLINE + REQUEST_ID,
    ]
    assert all(len(button.callback_data.encode()) <= 64
               for row in markup.inline_keyboard for button in row)
    assert markup_for(FIELD_WORK_REQUEST, "../../telegram/bot/outbox") is None
    assert markup_for(FIELD_WORK_REQUEST, None) is None


def test_confirmation_is_server_decision_not_fabricated_bot_checkin():
    call = Call(FIELD_WORK_CONFIRM + REQUEST_ID)
    client = Client()
    run(decide(call, client))
    assert client.calls == [{
        "telegram_user_id": 123,
        "request_id": REQUEST_ID,
        "decision": "CONFIRM",
    }]
    assert call.acks
    assert call.message.edits == 1
    assert "подтверждена" in call.message.answers[0][0]


def test_decline_does_not_count_day():
    call = Call(FIELD_WORK_DECLINE + REQUEST_ID)
    client = Client(status="DECLINED")
    run(decide(call, client))
    assert client.calls[0]["decision"] == "DECLINE"
    assert "не засчитан" in call.message.answers[0][0]


@pytest.mark.parametrize("data", [FIELD_WORK_CONFIRM + "../../outbox", FIELD_WORK_DECLINE + ""])
def test_invalid_callback_never_sent_with_bot_secret(data):
    call = Call(data)
    client = Client()
    run(decide(call, client))
    assert client.calls == []
    assert call.acks[0][1]["show_alert"] is True


def test_group_callback_does_not_act_for_group_member():
    call = Call(FIELD_WORK_CONFIRM + REQUEST_ID, Message(chat_id=-12, chat_type="supergroup"))
    client = Client()
    run(decide(call, client))
    assert client.calls == []


@pytest.mark.parametrize("status,reason,expected,clear", [
    (409, "closed_or_expired", "закрыт", 1),
    (409, "attendance_conflict", "Сейчас подтвердить нельзя", 0),
    (409, "schedule_changed", "Сейчас подтвердить нельзя", 0),
    (404, None, "недоступен", 1),
    (503, None, "Попробуйте", 0),
])
def test_backend_refusal_and_retry(status, reason, expected, clear):
    call = Call(FIELD_WORK_CONFIRM + REQUEST_ID)
    client = Client(error=ApiError(
        status, "conflict", "backend details must not leak",
        {"reason": reason} if reason else None,
    ))
    run(decide(call, client))
    assert call.message.edits == clear
    assert expected in call.message.answers[0][0]
    assert "backend details" not in call.message.answers[0][0]


def test_unexpected_success_does_not_claim_attendance_was_counted():
    call = Call(FIELD_WORK_CONFIRM + REQUEST_ID)
    run(decide(call, Client(status="PENDING")))
    assert call.message.edits == 0
    assert "Попробуйте" in call.message.answers[0][0]


def test_http_contract_uses_bot_secret_only_and_telegram_identity():
    client = SelfServiceClient("http://localhost:8000/api/v1")
    captured = {}

    async def fake_request(method, path, *, headers, json):
        captured.update(method=method, path=path, headers=headers, body=json)
        return {"status": "CONFIRMED"}

    client._request = fake_request
    run(client.field_work_decision(
        telegram_user_id=123, request_id=REQUEST_ID, decision="CONFIRM"
    ))
    assert captured == {
        "method": "POST",
        "path": f"/telegram/bot/field-work/{REQUEST_ID}/decision",
        "headers": {},
        "body": {"telegram_user_id": 123, "decision": "CONFIRM"},
    }
    with pytest.raises(ValueError):
        run(client.field_work_decision(
            telegram_user_id=123, request_id="../../outbox", decision="CONFIRM"
        ))


def test_sender_refuses_message_with_unusable_button():
    class Bot:
        async def send_message(self, *args, **kwargs):
            raise AssertionError("must not send without request UUID")

    result = run(TelegramSender(Bot()).deliver(
        chat_id=123, text="Выездная работа", notification_type=FIELD_WORK_REQUEST,
        entity_id=None,
    ))
    assert result.sent is False
    assert result.error == "invalid_entity_id"


def test_sender_delivers_plain_text_with_request_buttons():
    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kwargs):
            self.sent.append((chat_id, text, kwargs))

    bot = Bot()
    result = run(TelegramSender(bot).deliver(
        chat_id=123, text="Объект <терминал>",
        notification_type=FIELD_WORK_REQUEST, entity_id=REQUEST_ID,
    ))
    assert result.sent is True
    assert bot.sent[0][0:2] == (123, "Объект <терминал>")
    assert bot.sent[0][2]["parse_mode"] is None
    assert bot.sent[0][2]["reply_markup"].inline_keyboard[0][0].callback_data == (
        FIELD_WORK_CONFIRM + REQUEST_ID
    )
