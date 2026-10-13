"""«Написать HR»: что уходит на backend и что видит человек.

Два слоя проверок. Сначала — обработчики по отдельности: сеть и Telegram
подменены, клиент записывает вызовы, сообщение — ответы. Затем — весь
корневой роутер настоящего бота (`build_root_router`) с подставной
сессией Telegram: только так видно, какой обработчик на самом деле
получает кнопку меню во время ввода, команду или ответ на сообщение HR.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import datetime, timezone
from types import SimpleNamespace

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from src.api.errors import ApiError, BadRequest, ServerError
from src.handlers import build_root_router
from src.handlers.ask_hr.router import (
    ESCALATE,
    HR_HEADERS,
    LEGACY_HR_HEADER,
    RETRY,
    AskHr,
    ask_cancel,
    ask_send,
    ask_start,
)
from src.keyboards import employee as kb
from src.messages import employee as text

PROFILE = {"employee": {"full_name": "Иванов Иван"}}


# --- обработчики по отдельности ------------------------------------------------


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
        self.data: dict = {}

    async def set_state(self, value):
        self.value = value

    async def clear(self):
        self.value = None
        self.data = {}

    async def update_data(self, values=None, **kwargs):
        self.data.update(values or {}, **kwargs)
        return self.data

    async def get_data(self):
        return dict(self.data)


class FakeClient:
    """Ассистент и очередь HR — два разных адреса.

    `answer` — что ответил ассистент: `None` означает «ответа нет», и
    тогда бот обязан предложить кнопку, а не выдумывать. `errors` —
    очередь исходов для отправки HR: исключение или `None` (успех).
    """

    def __init__(self, errors=(), answer=None, ask_error=None):
        self.calls: list[dict] = []
        self.asked: list[dict] = []
        self.errors = list(errors)
        self.answer = answer
        self.ask_error = ask_error

    async def ask(self, telegram_id, *, text, client_request_id=None):
        self.asked.append({"telegram_id": telegram_id, "text": text})
        if self.ask_error:
            raise self.ask_error
        return self.answer or {"answered": False, "answer": None,
                               "sources": [], "status": "ESCALATED"}

    async def escalate(self, telegram_id, *, text, message_id=None, starts_question=False):
        self.calls.append({"telegram_id": telegram_id, "text": text,
                           "message_id": message_id,
                           "starts_question": starts_question})
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return {"question_id": "q-1", "number": 214, "status": "NEW", "created": True}


def test_the_button_invites_with_the_agreed_words():
    message, state = FakeMessage(kb.BTN_ASK_HR), FakeState()

    asyncio.run(ask_start(message, state, PROFILE, None))

    assert state.value == AskHr.waiting_text
    assert message.answers[-1][0] == (
        "Напишите сообщение для HR. После отправки оно сразу поступит в кадровый отдел."
    )


def test_without_a_binding_nothing_is_asked():
    message, state = FakeMessage(kb.BTN_ASK_HR), FakeState()

    asyncio.run(ask_start(message, state, None, "not_linked"))

    assert state.value is None
    assert message.answers[-1][0] == text.NOT_LINKED


def test_text_after_the_invitation_goes_to_hr_at_once():
    message, state, client = FakeMessage(), FakeState(), FakeClient()
    state.value = AskHr.waiting_text

    asyncio.run(ask_send(message, state, PROFILE, None, client))

    # Без ассистента, без подтверждения, с номером сообщения Telegram —
    # по нему backend не положит повтор второй раз.
    assert client.asked == []
    assert client.calls == [{"telegram_id": 555, "text": "Как перенести отпуск?",
                             "message_id": 314, "starts_question": True}]
    assert message.answers[-1][0] == "Сообщение отправлено HR."
    assert state.value is None


def test_cancel_sends_nothing():
    message, state = FakeMessage(kb.BTN_CANCEL), FakeState()
    state.value = AskHr.waiting_text

    asyncio.run(ask_cancel(message, state, PROFILE, None))

    assert state.value is None
    assert message.answers[-1][0] == text.ASK_HR_CANCELLED


def test_empty_photo_and_too_long_keep_waiting_and_send_nothing():
    for body, expected in (
        ("   ", text.ASK_HR_EMPTY),
        (None, text.ASK_HR_NOT_TEXT),
        ("а" * 4001, text.ASK_HR_TOO_LONG),
    ):
        message, state, client = FakeMessage(body), FakeState(), FakeClient()
        state.value = AskHr.waiting_text

        asyncio.run(ask_send(message, state, PROFILE, None, client))

        assert client.calls == []
        assert state.value == AskHr.waiting_text
        assert message.answers[-1][0] == expected


def test_the_headers_match_the_backend():
    """Разойдись заголовки с backend — ответы на сообщения HR пропадали бы."""
    assert HR_HEADERS == ("HR ответил на ваш вопрос", "Сообщение от HR")
    assert "💬 Ответ HR по обращению №214".startswith(LEGACY_HR_HEADER)


# --- весь бот: корневой роутер и подставной Telegram ----------------------------------


BOT_USER = User(id=4242, is_bot=True, first_name="HUMOTECH")
_ids = itertools.count(1)


class Telegram(BaseSession):
    """Подставной Telegram: записывает исходящие и отвечает как сервер."""

    def __init__(self):
        super().__init__()
        self.sent: list[SendMessage] = []

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.sent.append(method)
            return Message(
                message_id=9000 + len(self.sent),
                date=datetime.now(timezone.utc),
                chat=Chat(id=method.chat_id, type="private"),
                from_user=BOT_USER,
                text=method.text,
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    async def close(self):
        return None


#: Один корневой роутер на модуль: роутеры разделов — объекты модулей, и
#: подключить их ко второму корню aiogram не даст.
DISPATCHER = Dispatcher(storage=MemoryStorage())
DISPATCHER.include_router(build_root_router())


class Person:
    """Один человек в своём чате: шлёт сообщения и жмёт кнопки."""

    def __init__(self, client=None, employee=PROFILE, denial=None):
        self.user_id = 700000 + next(_ids)
        self.client = client or FakeClient()
        self.employee, self.denial = employee, denial
        self.session = Telegram()
        self.bot = Bot(token="42:TEST-ONLY-NOT-A-REAL-TOKEN-0000000000", session=self.session)
        self._message_ids = itertools.count(100)

    def _user(self):
        return User(id=self.user_id, is_bot=False, first_name="Иван")

    def _feed(self, update):
        async def run():
            await DISPATCHER.feed_update(
                self.bot, update, client=self.client,
                employee=self.employee, denial=self.denial,
            )
        asyncio.run(run())

    def say(self, body, *, reply_to=None, message_id=None):
        message = Message(
            message_id=message_id or next(self._message_ids),
            date=datetime.now(timezone.utc),
            chat=Chat(id=self.user_id, type="private"),
            from_user=self._user(),
            text=body,
            reply_to_message=reply_to,
        )
        update = Update(update_id=next(_ids), message=message)
        self._feed(update)
        return update

    def press(self, data):
        self._feed(Update(update_id=next(_ids), callback_query=CallbackQuery(
            id=str(next(_ids)), from_user=self._user(), chat_instance="ci",
            data=data,
            message=Message(
                message_id=next(self._message_ids), date=datetime.now(timezone.utc),
                chat=Chat(id=self.user_id, type="private"), from_user=BOT_USER,
                text="…",
            ),
        )))

    def replay(self, update):
        """Та же обработка того же апдейта ещё раз (повтор после сбоя)."""
        self._feed(update)

    def hr_message(self, header):
        return Message(
            message_id=next(self._message_ids), date=datetime.now(timezone.utc),
            chat=Chat(id=self.user_id, type="private"), from_user=BOT_USER,
            text=f"{header}\n\nНа какие даты вы планируете отпуск?",
        )

    @property
    def last(self) -> str:
        return self.session.sent[-1].text

    def state(self):
        async def read():
            key = StorageKey(bot_id=self.bot.id, chat_id=self.user_id, user_id=self.user_id)
            return await DISPATCHER.storage.get_state(key)
        return asyncio.run(read())


def test_bottom_button_then_text_goes_to_hr_without_confirmation():
    person = Person()

    person.say(kb.BTN_ASK_HR)
    assert person.last == text.ASK_HR_PROMPT
    person.say("Как оформить отпуск?", message_id=501)

    assert person.client.calls == [{"telegram_id": person.user_id, "text": "Как оформить отпуск?",
                                    "message_id": 501, "starts_question": True}]
    assert person.last == "Сообщение отправлено HR."
    assert person.state() is None


def test_the_old_button_label_still_works():
    person = Person()

    person.say(kb.BTN_ASK_HR_LEGACY)
    person.say("Нужна справка 2-НДФЛ")

    assert [one["text"] for one in person.client.calls] == ["Нужна справка 2-НДФЛ"]


def test_after_sending_the_next_text_does_not_go_to_hr():
    person = Person()
    person.say(kb.BTN_ASK_HR)
    person.say("Первый вопрос")

    person.say("Спасибо")

    # Ожидание закрыто: «Спасибо» — уже не сообщение HR, а вопрос в чат.
    assert len(person.client.calls) == 1
    assert person.client.asked[-1]["text"] == "Спасибо"


def test_assistant_button_invites_and_does_not_send_the_question():
    person = Person()

    person.say("Сколько дней отпуска у стажёра?", message_id=777)
    assert person.last == text.ASK_NO_ANSWER
    button = person.session.sent[-1].reply_markup.inline_keyboard[0][0]
    assert button.text == "Написать HR"
    person.press(button.callback_data)

    # Нажатие ничего не отправляет: HR получит то, что человек напишет.
    assert person.client.calls == []
    assert person.last == text.ASK_HR_PROMPT
    person.say("Мне нужно 5 дней в ноябре, как оформить?", message_id=778)

    assert [(one["text"], one["message_id"]) for one in person.client.calls] == [
        ("Мне нужно 5 дней в ноябре, как оформить?", 778)
    ]
    assert person.last == "Сообщение отправлено HR."


def test_an_old_pass_to_hr_button_opens_the_invitation():
    person = Person()

    person.press(f"{ESCALATE}999")

    assert person.client.calls == []
    assert person.last == text.ASK_HR_PROMPT


def test_menu_button_while_typing_works_as_menu_and_ends_typing():
    person = Person()
    person.say(kb.BTN_ASK_HR)

    person.say(kb.BTN_HELP)
    assert person.last == text.HELP
    person.say("а это просто текст")

    assert person.client.calls == []
    assert person.state() is None


def test_a_command_while_typing_is_not_text_for_hr():
    person = Person()
    person.say(kb.BTN_ASK_HR)

    person.say("/help")

    assert person.client.calls == []
    assert person.last == text.HELP
    assert person.state() is None


def test_cancel_command_and_button_send_nothing():
    for word in ("/cancel", kb.BTN_CANCEL):
        person = Person()
        person.say(kb.BTN_ASK_HR)
        person.say(word)

        assert person.client.calls == []
        assert person.last == text.ASK_HR_CANCELLED
        assert person.state() is None


def test_pressing_write_to_hr_twice_is_one_scenario_and_sends_nothing():
    person = Person()

    person.say(kb.BTN_ASK_HR)
    person.say(kb.BTN_ASK_HR)
    person.press(f"{ESCALATE}1")

    assert person.client.calls == []
    person.say("Один текст")
    assert len(person.client.calls) == 1


def test_a_failure_is_not_reported_as_success_and_retry_sends_the_same_message():
    person = Person(client=FakeClient(errors=[ServerError(503, "unavailable", "нет связи")]))
    person.say(kb.BTN_ASK_HR)

    person.say("Где мой расчётный лист?", message_id=640)

    assert person.last == text.ASK_HR_FAILED
    button = person.session.sent[-1].reply_markup.inline_keyboard[0][0]
    assert button.callback_data == f"{RETRY}640"
    person.press(button.callback_data)

    # Тот же номер сообщения: если первая попытка всё-таки легла, backend
    # второй раз её не положит.
    assert [(one["text"], one["message_id"]) for one in person.client.calls] == [
        ("Где мой расчётный лист?", 640), ("Где мой расчётный лист?", 640),
    ]
    assert person.last == "Сообщение отправлено HR."

    person.press(button.callback_data)
    assert len(person.client.calls) == 2
    assert person.last == text.ASK_HR_RETRY_GONE


def test_a_lost_response_is_retried_with_the_same_message_id():
    # Сервер сохранил, а ответ не дошёл: таймаут — не успех и не провал.
    person = Person(client=FakeClient(errors=[asyncio.TimeoutError(),
                                              aiohttp.ClientConnectionError()]))
    person.say(kb.BTN_ASK_HR)
    person.say("Справка с места работы", message_id=900)
    assert person.last == text.ASK_HR_FAILED
    person.press(f"{RETRY}900")
    assert person.last == text.ASK_HR_FAILED
    person.press(f"{RETRY}900")

    assert [one["message_id"] for one in person.client.calls] == [900, 900, 900]
    assert person.last == "Сообщение отправлено HR."


def test_a_rejected_text_asks_for_another_and_keeps_the_scenario():
    person = Person(client=FakeClient(errors=[BadRequest(400, "validation_failed", "текст")]))
    person.say(kb.BTN_ASK_HR)

    person.say("\u0007")

    assert person.last == text.ASK_HR_REJECTED
    assert person.state() == AskHr.waiting_text.state
    person.say("Нормальный текст")
    assert person.last == "Сообщение отправлено HR."


def test_the_same_update_processed_twice_reaches_hr_once():
    person = Person()
    person.say(kb.BTN_ASK_HR)
    update = person.say("Вопрос про больничный", message_id=321)

    person.replay(update)

    assert [one["message_id"] for one in person.client.calls] == [321]


def test_two_different_messages_with_the_same_words_are_both_sent():
    person = Person()

    person.say(kb.BTN_ASK_HR)
    person.say("Перезвоните мне", message_id=11)
    person.say(kb.BTN_ASK_HR)
    person.say("Перезвоните мне", message_id=12)

    assert [one["message_id"] for one in person.client.calls] == [11, 12]


def test_a_reply_to_any_hr_message_continues_the_conversation():
    for header in (*HR_HEADERS, f"{LEGACY_HR_HEADER}12"):
        person = Person()

        person.say("С 3 по 7 ноября", reply_to=person.hr_message(header))

        assert person.client.asked == []
        assert person.client.calls[0]["text"] == "С 3 по 7 ноября"
        # Продолжение разговора — не новый вопрос.
        assert person.client.calls[0]["starts_question"] is False
        assert person.last == "Сообщение отправлено HR."


def test_a_reply_to_ones_own_message_is_not_taken_for_hr():
    person = Person()
    own = Message(
        message_id=5, date=datetime.now(timezone.utc),
        chat=Chat(id=person.user_id, type="private"),
        from_user=User(id=person.user_id, is_bot=False, first_name="Иван"),
        text="Сообщение от HR пришло?",
    )

    person.say("Ну что там?", reply_to=own)

    assert person.client.calls == []
    assert person.client.asked[-1]["text"] == "Ну что там?"


def test_without_a_binding_nothing_reaches_hr():
    person = Person(employee=None, denial="not_linked")

    person.say(kb.BTN_ASK_HR)
    person.say("Привет")

    assert person.client.calls == []
    assert person.client.asked == []


def test_a_broken_assistant_still_offers_hr():
    person = Person(client=FakeClient(ask_error=ApiError(503, "unavailable", "нет связи")))

    person.say("Где взять бланк?")

    assert person.last == text.ASK_NO_ANSWER
