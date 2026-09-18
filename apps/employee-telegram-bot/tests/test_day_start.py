"""Ответ на напоминание о начале дня.

Проверяется то, из-за чего такой разговор превращается в тупик:

— нажатие, на которое бот промолчал;
— причина, которую нельзя пропустить;
— «не приду», после которого человек уверен, что больничный открыт;
— второй ответ, ушедший на сервер после первого;
— сбой сети, оставивший человека без ответа.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.api.errors import ApiError
from src.handlers.day_start.router import (
    DayStart,
    ask_reason,
    mark_now,
    reason_typed,
    skip_reason,
)
from src.messages import day_start as text
from src.notifications.buttons import (
    DAY_START,
    SAY_ABSENT,
    SAY_LATE,
    markup_for,
)


class FakeMessage:
    def __init__(self, body: str | None = "Пробки"):
        self.from_user = SimpleNamespace(id=555, username="ivan")
        self.chat = SimpleNamespace(id=555, type="private")
        self.text = body
        self.answers: list[tuple[str, object]] = []

    async def answer(self, body, reply_markup=None, **kwargs):
        self.answers.append((body, reply_markup))


class FakeCall:
    def __init__(self, data: str):
        self.data = data
        self.from_user = SimpleNamespace(id=555)
        self.message = FakeMessage()
        self.answered = False

    async def answer(self, *args, **kwargs):
        self.answered = True


class FakeState:
    def __init__(self, data: dict | None = None):
        self.value = None
        self.data = dict(data or {})

    async def set_state(self, value):
        self.value = value

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.value = None
        self.data = {}


class FakeClient:
    def __init__(self, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error

    async def day_notice(self, *, telegram_user_id, kind, comment=None):
        self.calls.append({"telegram_user_id": telegram_user_id,
                           "kind": kind, "comment": comment})
        if self.error:
            raise self.error
        return {"kind": kind, "comment": comment, "day": "2026-03-10"}


def run(coro):
    return asyncio.run(coro)


# --- кнопки ------------------------------------------------------------------


def test_reminder_carries_three_answers(monkeypatch):
    monkeypatch.setattr(
        "src.notifications.buttons.settings",
        SimpleNamespace(mini_app_url="https://example.test"),
    )
    markup = markup_for(DAY_START, None)

    assert markup is not None
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["Отметиться сейчас", "Опаздываю", "Не приду"]
    # Путь к отметке короче, чем к объяснению: чаще всего человек просто
    # забыл приложить телефон.
    assert markup.inline_keyboard[0][0].web_app is not None


def test_without_mini_app_the_button_still_answers(monkeypatch):
    monkeypatch.setattr(
        "src.notifications.buttons.settings",
        SimpleNamespace(mini_app_url=""),
    )
    markup = markup_for(DAY_START, None)

    # Кнопка, которая ничего не открывает, хуже её отсутствия — но
    # молчание на нажатие хуже обеих.
    assert markup.inline_keyboard[0][0].web_app is None
    assert markup.inline_keyboard[0][0].callback_data


def test_other_notifications_keep_their_markup():
    assert markup_for("absence.approved", None) is None


# --- разговор ----------------------------------------------------------------


def test_late_asks_for_a_reason_and_allows_skipping():
    call = FakeCall(SAY_LATE)
    state = FakeState()

    run(ask_reason(call, state))

    assert call.answered
    assert state.value == DayStart.waiting_reason
    assert state.data["day_notice_kind"] == "LATE"
    body, markup = call.message.answers[0]
    assert "Без причины" in str(markup)
    assert body == text.ASK_LATE_REASON


def test_typed_reason_reaches_the_server():
    message = FakeMessage("Пробки на Амира Темура")
    state = FakeState({"day_notice_kind": "LATE"})
    client = FakeClient()

    run(reason_typed(message, state, client))

    assert client.calls == [{
        "telegram_user_id": 555, "kind": "LATE",
        "comment": "Пробки на Амира Темура",
    }]
    assert message.answers[0][0] == text.LATE_SAVED
    assert state.value is None


def test_reason_can_be_skipped():
    call = FakeCall(text.SKIP)
    state = FakeState({"day_notice_kind": "LATE"})
    client = FakeClient()

    run(skip_reason(call, state, client))

    # Требовать объяснение у того, кто стоит в пробке, — способ не
    # получить ни объяснения, ни предупреждения.
    assert client.calls[0]["comment"] is None


def test_empty_message_keeps_the_conversation_open():
    message = FakeMessage(None)
    state = FakeState({"day_notice_kind": "LATE"})
    client = FakeClient()

    run(reason_typed(message, state, client))

    # Человек прислал фото вместо текста и всё ещё хочет предупредить.
    assert client.calls == []
    assert state.data["day_notice_kind"] == "LATE"
    assert message.answers[0][0] == text.REASON_EMPTY


def test_absent_answer_says_the_request_is_still_needed():
    message = FakeMessage("Заболел")
    state = FakeState({"day_notice_kind": "ABSENT"})
    client = FakeClient()

    run(reason_typed(message, state, client))

    assert client.calls[0]["kind"] == "ABSENT"
    body = message.answers[0][0]
    # Иначе человек уйдёт уверенным, что больничный уже открыт, и не
    # подаст заявку.
    assert "оформите заявку" in body


def test_network_failure_is_told_to_the_person():
    message = FakeMessage("Пробки")
    state = FakeState({"day_notice_kind": "LATE"})
    client = FakeClient(error=ApiError(500, "сломалось", {}))

    run(reason_typed(message, state, client))

    assert message.answers[0][0] == text.FAILED


def test_mark_button_without_mini_app_gives_a_way_forward():
    call = FakeCall("day:mark")

    run(mark_now(call))

    assert call.answered
    assert call.message.answers[0][0] == text.MARK_HINT
