"""«Написать в HR»: что уходит на backend и что видит человек.

Сеть и Telegram подменены: клиент записывает вызовы, сообщение —
ответы, состояние — переходы.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.api.errors import ApiError
from src.handlers.ask_hr.router import (
    REPLY_PREFIX,
    AskHr,
    ask_cancel,
    ask_send,
    ask_start,
    reply_to_hr,
)
from src.keyboards import employee as kb
from src.messages import employee as text

PROFILE = {"employee": {"full_name": "Иванов Иван"}}


class FakeMessage:
    def __init__(self, body="Как перенести отпуск?", message_id=314):
        self.from_user = SimpleNamespace(id=555, username="ivan", language_code="ru")
        self.chat = SimpleNamespace(id=555, type="private")
        self.text = body
        self.message_id = message_id
        self.answers: list[tuple[str, object]] = []

    async def answer(self, body, reply_markup=None, **kwargs):
        self.answers.append((body, reply_markup))


class FakeState:
    def __init__(self):
        self.value = None

    async def set_state(self, value):
        self.value = value

    async def clear(self):
        self.value = None


class FakeClient:
    def __init__(self, result=None, error=None):
        self.calls: list[dict] = []
        self.result = result or {"question_id": "q-1", "number": 214,
                                 "status": "NEW", "created": True}
        self.error = error

    async def ask_hr(self, telegram_id, *, text, message_id=None):
        self.calls.append({"telegram_id": telegram_id, "text": text,
                           "message_id": message_id})
        if self.error:
            raise self.error
        return self.result


def test_the_button_waits_for_one_message():
    message, state = FakeMessage(kb.BTN_ASK_HR), FakeState()

    asyncio.run(ask_start(message, state, PROFILE, None))

    assert state.value == AskHr.waiting_text
    assert message.answers[-1][0] == text.ASK_HR_PROMPT


def test_without_a_binding_nothing_is_asked():
    message, state = FakeMessage(kb.BTN_ASK_HR), FakeState()

    asyncio.run(ask_start(message, state, None, "not_linked"))

    assert state.value is None
    assert message.answers[-1][0] == text.NOT_LINKED


def test_the_question_goes_to_backend_with_its_message_id():
    message, state, client = FakeMessage(), FakeState(), FakeClient()
    state.value = AskHr.waiting_text

    asyncio.run(ask_send(message, state, PROFILE, None, client))

    assert client.calls == [{"telegram_id": 555, "text": "Как перенести отпуск?",
                             "message_id": 314}]
    assert state.value is None
    assert "№214" in message.answers[-1][0]


def test_a_follow_up_says_it_was_added():
    message, state = FakeMessage(), FakeState()
    client = FakeClient(result={"question_id": "q-1", "number": 214,
                                "status": "IN_PROGRESS", "created": False})

    asyncio.run(ask_send(message, state, PROFILE, None, client))

    assert message.answers[-1][0].startswith("Добавили сообщение в обращение №214")


def test_a_failure_does_not_pretend_the_question_was_sent():
    message, state = FakeMessage(), FakeState()
    client = FakeClient(error=ApiError(503, "unavailable", "нет связи"))

    asyncio.run(ask_send(message, state, PROFILE, None, client))

    assert message.answers[-1][0] == text.ASK_HR_FAILED


def test_a_photo_instead_of_text_keeps_waiting():
    message, state, client = FakeMessage(body=None), FakeState(), FakeClient()
    state.value = AskHr.waiting_text

    asyncio.run(ask_send(message, state, PROFILE, None, client))

    assert client.calls == []
    assert state.value == AskHr.waiting_text


def test_cancel_sends_nothing():
    message, state = FakeMessage(kb.BTN_CANCEL), FakeState()
    state.value = AskHr.waiting_text

    asyncio.run(ask_cancel(message, state, PROFILE, None))

    assert state.value is None
    assert message.answers[-1][0] == text.ASK_HR_CANCELLED


def test_replying_to_hr_goes_to_the_same_question():
    message, client = FakeMessage("Даты: 3–5 октября"), FakeClient()

    asyncio.run(reply_to_hr(message, PROFILE, None, client))

    assert client.calls[0]["text"] == "Даты: 3–5 октября"


def test_the_prefix_matches_the_backend_header():
    """Разойдись префикс с backend — ответы на сообщения HR пропадали бы."""
    assert "💬 Ответ HR по обращению №214".startswith(REPLY_PREFIX)
