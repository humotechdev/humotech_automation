"""Отметка по печатному QR-коду: `/start qr_…` -> геопозиция -> backend.

Бот здесь не решает, пускать ли человека, — это делает backend. Бот
отвечает за порядок разговора и за две вещи, которые видны только ему:
пересланная геопозиция — не текущая, а геопозиция, пришедшая сильно
позже скана, — уже не у двери.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.handlers.attendance import sticker
from src.messages import attendance as text

TG_ID = 555
SECRET = "qr_" + "A" * 43


class FakeState:
    def __init__(self, data=None, state=None):
        self.data = dict(data or {})
        self.state = state

    async def set_state(self, value):
        self.state = value

    async def update_data(self, **values):
        self.data.update(values)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.data = {}
        self.state = None


class FakeMessage:
    def __init__(self, *, location=None, forwarded=False, sent=None):
        self.from_user = SimpleNamespace(id=TG_ID)
        self.chat = SimpleNamespace(id=TG_ID, type="private")
        self.message_id = 42
        self.date = sent or datetime.now(tz=timezone.utc)
        self.location = location
        self.forward_origin = object() if forwarded else None
        self.answers: list[str] = []

    async def answer(self, body, reply_markup=None, **kwargs):
        self.answers.append(body)


class FakeClient:
    def __init__(self, result=None):
        self.calls: list[dict] = []
        self.result = result or {
            "status": "ENTERED", "accepted": True,
            "office_name": "Ташкент Сити", "point_name": "Главный вход",
            "point_mode": "ENTRY", "distance_m": 23, "radius_m": 100,
            "occurred_at_local": "09:02", "session": None,
        }

    async def scan(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture(autouse=True)
def address(monkeypatch):
    from src.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "mini_app_url", "https://mini.example", False
    )


def place(accuracy=12.0):
    return SimpleNamespace(latitude=41.3113, longitude=69.2406,
                           horizontal_accuracy=accuracy)


# --- разбор ------------------------------------------------------------------

def test_a_sticker_payload_is_recognised():
    assert sticker.sticker_payload(SECRET) == SECRET
    assert sticker.sticker_payload("link_abc") is None
    assert sticker.sticker_payload("qr_") is None
    assert sticker.sticker_payload(None) is None


# --- разговор ----------------------------------------------------------------

def test_scanning_a_sticker_asks_for_the_location():
    message = FakeMessage()
    state = FakeState()

    asyncio.run(sticker.begin(message, SECRET, state, employee=object(), denial=None))

    assert state.state == sticker.StickerScan.waiting_location
    assert state.data["sticker"] == SECRET
    assert message.answers == [text.ASK_LOCATION]


def test_the_location_goes_to_the_backend_with_the_sticker():
    message = FakeMessage(location=place())
    state = FakeState({"sticker": SECRET, "asked_at": time.time()},
                      sticker.StickerScan.waiting_location)
    client = FakeClient()

    asyncio.run(sticker.location(message, state, client))

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["token"] == SECRET
    assert call["telegram_user_id"] == TG_ID
    assert call["accuracy_m"] == 12.0
    assert state.state is None
    reply = message.answers[-1]
    assert "Расстояние до офиса: 23 м" in reply
    assert "Время: 09:02" in reply


def test_a_forwarded_location_is_not_accepted():
    message = FakeMessage(location=place(), forwarded=True)
    state = FakeState({"sticker": SECRET, "asked_at": time.time()},
                      sticker.StickerScan.waiting_location)
    client = FakeClient()

    asyncio.run(sticker.location(message, state, client))

    assert client.calls == []
    assert message.answers == [text.LOCATION_FORWARDED]
    # Ждём дальше: человек нажмёт кнопку и пришлёт свою.
    assert state.state == sticker.StickerScan.waiting_location


def test_a_location_long_after_the_scan_is_not_accepted():
    message = FakeMessage(location=place())
    state = FakeState({"sticker": SECRET, "asked_at": time.time() - 600},
                      sticker.StickerScan.waiting_location)
    client = FakeClient()

    asyncio.run(sticker.location(message, state, client))

    assert client.calls == []
    assert message.answers == [text.LOCATION_EXPIRED]


def test_an_old_location_message_is_not_accepted():
    message = FakeMessage(location=place(),
                          sent=datetime.now(tz=timezone.utc) - timedelta(minutes=5))
    state = FakeState({"sticker": SECRET, "asked_at": time.time()},
                      sticker.StickerScan.waiting_location)
    client = FakeClient()

    asyncio.run(sticker.location(message, state, client))

    assert client.calls == []


# --- тексты ------------------------------------------------------------------

def test_too_far_says_how_far_and_what_is_allowed():
    body = text.outcome({"status": "OUTSIDE_GEOFENCE", "distance_m": 184,
                         "radius_m": 100})
    assert "Вы слишком далеко от офиса: 184 м. Допустимо: 100 м" in body


def test_a_reissued_code_is_called_no_longer_valid():
    assert "Этот QR-код больше не действует" in text.outcome({"status": "QR_REVOKED"})


def test_an_entry_point_explains_its_direction():
    body = text.outcome({"status": "ALREADY_INSIDE", "point_mode": "ENTRY"})
    assert "предназначена только для входа" in body


def test_an_exit_point_explains_its_direction():
    body = text.outcome({"status": "NOT_INSIDE", "point_mode": "EXIT"})
    assert "предназначена только для выхода" in body
