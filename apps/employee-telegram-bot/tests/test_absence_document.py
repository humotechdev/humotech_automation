"""Загрузка справки к заявке из чата.

Проверяется то, из-за чего такой разговор заканчивается ничем:

— кнопка, не знающая, к какой заявке прикладывать;
— превью вместо снимка в полном размере;
— текст, принятый за справку;
— отказ сервера, пересказанный своими словами;
— состояние, снятое до того, как файл ушёл.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.api.errors import ApiError
from src.handlers.absence_document.router import Upload, ask_file, cancel, receive
from src.messages import absence_document as text
from src.notifications.buttons import (
    DOCUMENT_REJECTED,
    UPLOAD_DOCUMENT,
    markup_for,
)

REQUEST_ID = "11111111-2222-3333-4444-555555555555"


class FakeStream:
    def __init__(self, data: bytes = b"%PDF-1.4"):
        self.data = data

    def read(self) -> bytes:
        return self.data


class FakeBot:
    def __init__(self, error: Exception | None = None):
        self.downloaded: list[str] = []
        self.error = error

    async def download(self, file_id: str):
        self.downloaded.append(file_id)
        if self.error:
            raise self.error
        return FakeStream()


class FakeMessage:
    def __init__(self, *, document=None, photo=None, bot=None):
        self.from_user = SimpleNamespace(id=555, username="ivan")
        self.chat = SimpleNamespace(id=555, type="private")
        self.text = None
        self.document = document
        self.photo = photo or []
        self.bot = bot or FakeBot()
        self.answers: list[str] = []

    async def answer(self, body, reply_markup=None, **kwargs):
        self.answers.append(body)


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

    async def upload_absence_document(
        self, *, telegram_user_id, request_id, filename, content, content_type
    ):
        self.calls.append({
            "telegram_user_id": telegram_user_id, "request_id": request_id,
            "filename": filename, "content": content, "content_type": content_type,
        })
        if self.error:
            raise self.error
        return {"id": "d-1"}


def run(coro):
    return asyncio.run(coro)


def doc(name="spravka.pdf", mime="application/pdf", size=1024):
    return SimpleNamespace(
        file_id="f-1", file_name=name, mime_type=mime, file_size=size
    )


def photo_sizes():
    # Telegram отдаёт размеры по возрастанию: первый — превью.
    return [
        SimpleNamespace(file_id="small", file_size=1000),
        SimpleNamespace(file_id="large", file_size=90000),
    ]


# --- кнопка ------------------------------------------------------------------


def test_rejection_carries_an_upload_button():
    markup = markup_for(DOCUMENT_REJECTED, REQUEST_ID)

    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.text == "Загрузить справку"
    # Заявку кнопка знает сама: у человека их бывает несколько, и
    # выбирать из списка после отказа — лишний шаг.
    assert button.callback_data == f"{UPLOAD_DOCUMENT}{REQUEST_ID}"


def test_rejection_without_a_request_has_no_button():
    # Кнопка приложила бы справку «куда-нибудь».
    assert markup_for(DOCUMENT_REJECTED, None) is None


def test_callback_data_fits_the_telegram_limit():
    # Шестьдесят четыре байта — потолок Telegram; префикс плюс UUID
    # укладываются, и проверить это дешевле, чем поймать в бою.
    data = f"{UPLOAD_DOCUMENT}{REQUEST_ID}"
    assert len(data.encode()) <= 64


# --- разговор ----------------------------------------------------------------


def test_button_starts_waiting_for_a_file():
    call = FakeCall(f"{UPLOAD_DOCUMENT}{REQUEST_ID}")
    state = FakeState()

    run(ask_file(call, state))

    assert call.answered
    assert state.value == Upload.waiting_file
    assert state.data["absence_request_id"] == REQUEST_ID
    assert call.message.answers[0] == text.ASK_FILE


def test_document_reaches_the_server():
    message = FakeMessage(document=doc())
    state = FakeState({"absence_request_id": REQUEST_ID})
    client = FakeClient()

    run(receive(message, state, client))

    assert client.calls[0]["request_id"] == REQUEST_ID
    assert client.calls[0]["filename"] == "spravka.pdf"
    assert client.calls[0]["content"] == b"%PDF-1.4"
    assert message.answers[0] == text.SAVED
    assert state.value is None


def test_photo_is_taken_at_full_size():
    message = FakeMessage(photo=photo_sizes())
    state = FakeState({"absence_request_id": REQUEST_ID})
    client = FakeClient()

    run(receive(message, state, client))

    # Первый размер — превью: справка на нём нечитаема.
    assert message.bot.downloaded == ["large"]
    assert client.calls[0]["content_type"] == "image/jpeg"


def test_oversized_file_is_refused_without_downloading():
    message = FakeMessage(document=doc(size=text.MAX_BYTES + 1))
    state = FakeState({"absence_request_id": REQUEST_ID})
    client = FakeClient()

    run(receive(message, state, client))

    assert message.bot.downloaded == []
    assert client.calls == []
    # Состояние остаётся: человек пришлёт снимок поменьше.
    assert state.data["absence_request_id"] == REQUEST_ID
    assert message.answers[0] == text.TOO_BIG


def test_server_refusal_is_shown_in_its_own_words():
    message = FakeMessage(document=doc())
    state = FakeState({"absence_request_id": REQUEST_ID})
    client = FakeClient(
        error=ApiError(409, "conflict", "Заявка закрыта", {})
    )

    run(receive(message, state, client))

    # Сервер объясняет, что именно не так; пересказ своими словами
    # однажды превратит «заявка закрыта» в «попробуйте ещё раз».
    assert message.answers[0] == "Заявка закрыта"


def test_download_failure_does_not_lose_the_person():
    message = FakeMessage(document=doc(), bot=FakeBot(error=RuntimeError("нет")))
    state = FakeState({"absence_request_id": REQUEST_ID})
    client = FakeClient()

    run(receive(message, state, client))

    assert client.calls == []
    assert message.answers[0] == text.FAILED


def test_cancel_clears_the_waiting():
    message = FakeMessage()
    state = FakeState({"absence_request_id": REQUEST_ID})

    run(cancel(message, state))

    assert state.value is None
    assert message.answers[0] == text.CANCELLED
