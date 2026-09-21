"""«Написать в HR»: что уходит на backend и что видит человек.

Сеть и Telegram подменены: клиент записывает вызовы, сообщение —
ответы, состояние — переходы.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.api.errors import ApiError
from src.handlers.ask_hr.router import (
    ESCALATE,
    REPLY_PREFIX,
    AskHr,
    ask_cancel,
    ask_send,
    ask_start,
    escalate,
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


class FakeCall:
    """Нажатие «Передать HR»."""

    def __init__(self, data: str):
        self.data = data
        self.from_user = SimpleNamespace(id=555)
        self.message = FakeMessage()
        self.answered = False

    async def answer(self, *args, **kwargs):
        self.answered = True


class FakeClient:
    """Ассистент и очередь HR — два разных адреса.

    `answer` описывает, что ответил ассистент: `None` означает «ответа
    нет», и тогда бот обязан предложить кнопку, а не выдумывать.
    """

    def __init__(self, result=None, error=None, answer=None, ask_error=None):
        self.calls: list[dict] = []
        self.asked: list[dict] = []
        self.result = result or {"question_id": "q-1", "number": 214,
                                 "status": "NEW", "created": True}
        self.error = error
        self.answer = answer
        self.ask_error = ask_error

    async def ask(self, telegram_id, *, text, client_request_id=None):
        self.asked.append({"telegram_id": telegram_id, "text": text})
        if self.ask_error:
            raise self.ask_error
        return self.answer or {"answered": False, "answer": None,
                               "sources": [], "status": "ESCALATED"}

    async def escalate(self, telegram_id, *, text, message_id=None):
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


def test_the_question_goes_to_the_assistant_first():
    # Вопрос, на который отвечают правила компании, не должен попадать
    # в очередь кадровика: там его будут просматривать, а не искать.
    message, state, client = FakeMessage(), FakeState(), FakeClient()
    state.value = AskHr.waiting_text

    asyncio.run(ask_send(message, state, PROFILE, None, client))

    assert client.asked == [{"telegram_id": 555, "text": "Как перенести отпуск?"}]
    # Обращение НЕ создано: человек ещё не просил передавать.
    assert client.calls == []
    assert state.value is None


def test_a_confident_answer_is_shown_with_its_source():
    message, state = FakeMessage(), FakeState()
    client = FakeClient(answer={
        "answered": True,
        "answer": "Отпуск переносится заявлением за две недели.",
        "sources": ["Правила предоставления отпусков"],
        "status": "RAG_ANSWERED",
    })

    asyncio.run(ask_send(message, state, PROFILE, None, client))

    body = message.answers[-1][0]
    assert "Отпуск переносится заявлением" in body
    # Ответ без ссылки на правило — это мнение, а мнению в кадровом
    # вопросе верить нельзя.
    assert "Правила предоставления отпусков" in body
    assert client.calls == []


def test_without_an_answer_the_bot_offers_the_button_and_invents_nothing():
    message, state, client = FakeMessage(), FakeState(), FakeClient()

    asyncio.run(ask_send(message, state, PROFILE, None, client))

    body, markup = message.answers[-1]
    assert body == text.ASK_NO_ANSWER
    assert markup is not None
    assert client.calls == []


def test_escalation_sends_the_original_question():
    message, state, client = FakeMessage(), FakeState(), FakeClient()
    asyncio.run(ask_send(message, state, PROFILE, None, client))

    call = FakeCall(f"{ESCALATE}314")
    asyncio.run(escalate(call, client))

    # Кадровик должен прочитать то, что написал человек, а не пересказ
    # ассистента и не его неудачный ответ.
    assert client.calls[0]["text"] == "Как перенести отпуск?"
    assert call.message.answers[-1][0] == text.ASK_ESCALATED


def test_escalation_after_a_restart_says_so():
    client = FakeClient()
    call = FakeCall(f"{ESCALATE}999")

    asyncio.run(escalate(call, client))

    # Молчать нельзя: человек нажал кнопку и ждёт.
    assert client.calls == []
    assert call.message.answers[-1][0] == text.ASK_ESCALATE_LOST


def test_a_broken_assistant_still_offers_hr():
    # Недоступный ассистент не должен оставлять человека без пути вперёд.
    message, state = FakeMessage(), FakeState()
    client = FakeClient(ask_error=ApiError(503, "unavailable", "нет связи"))

    asyncio.run(ask_send(message, state, PROFILE, None, client))

    assert message.answers[-1][0] == text.ASK_NO_ANSWER


def test_a_failure_of_escalation_does_not_pretend_it_was_sent():
    message, state = FakeMessage(), FakeState()
    client = FakeClient(error=ApiError(503, "unavailable", "нет связи"))
    asyncio.run(ask_send(message, state, PROFILE, None, client))

    call = FakeCall(f"{ESCALATE}314")
    asyncio.run(escalate(call, client))

    assert call.message.answers[-1][0] == text.ASK_ESCALATE_FAILED


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


def test_replying_to_hr_goes_straight_to_the_question():
    # Разговор с кадровиком уже идёт: вклиниваться в него ответом из
    # правил — значит перебивать.
    message, client = FakeMessage("Даты: 3–5 октября"), FakeClient()

    asyncio.run(reply_to_hr(message, PROFILE, None, client))

    assert client.asked == []
    assert client.calls[0]["text"] == "Даты: 3–5 октября"
    assert "№214" in message.answers[-1][0]


def test_the_prefix_matches_the_backend_header():
    """Разойдись префикс с backend — ответы на сообщения HR пропадали бы."""
    assert "💬 Ответ HR по обращению №214".startswith(REPLY_PREFIX)
