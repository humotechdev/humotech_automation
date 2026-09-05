"""Отметка, пришедшая служебным сообщением от Mini App.

Всё содержимое `web_app_data` — недоверенные данные: изменённый клиент
Telegram может отправить туда что угодно. Здесь проверяется ровно
граница доверия, а не «работает ли счастливый путь».

Главное свойство: единственный Telegram ID, которому верят, —
`message.from_user.id`. Его проставил сам Telegram, доставляя сообщение.
Всё, что назвалось идентификатором внутри JSON, обязано заканчиваться
отказом, а не тихим игнорированием: молча выбросить поле значит не
заметить нападение.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.api.errors import ApiError, ServerError
from src.handlers.attendance.payload import Rejected, parse
from src.handlers.attendance.router import scan_from_mini_app
from src.messages import attendance as text

TG_ID = 555


def payload(**over) -> str:
    body = {
        "version": 1,
        "action": "attendance_scan",
        "qr": "HT1.код-с-экрана",
        "client_event_id": "attempt-1",
        "location": {
            "latitude": 38.5602,
            "longitude": 68.7874,
            "accuracy": 15.0,
        },
    }
    body.update(over)
    return json.dumps(body)


class FakeMessage:
    def __init__(self, data: str, *, user_id=TG_ID, chat_type="private"):
        self.from_user = SimpleNamespace(id=user_id, username="ivan",
                                         language_code="ru")
        self.chat = SimpleNamespace(id=user_id, type=chat_type)
        self.web_app_data = SimpleNamespace(data=data, button_text="📷")
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text_body, reply_markup=None, **kwargs):
        self.answers.append((text_body, reply_markup))


class FakeClient:
    """Backend. Помнит вызовы отметки, отдаёт заготовленный ответ."""

    def __init__(self, *, result=None, raises=None):
        self.calls: list[dict] = []
        self.result = result or {
            "status": "ENTERED", "accepted": True,
            "office_name": "Ташкентский офис", "point_name": "Главный вход",
            "occurred_at": "2026-09-05T04:03:00Z", "session": None,
        }
        self.raises = raises

    async def scan(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.result


def run(message, client=None):
    client = client or FakeClient()
    asyncio.run(scan_from_mini_app(message, client))
    return client


@pytest.fixture(autouse=True)
def address(monkeypatch):
    from src.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "mini_app_url", "https://mini.example", False
    )


# --- разбор payload ---------------------------------------------------------

def test_a_correct_payload_parses():
    attempt = parse(payload())

    assert attempt.qr == "HT1.код-с-экрана"
    assert attempt.client_event_id == "attempt-1"
    assert attempt.accuracy_m == 15.0


@pytest.mark.parametrize(
    "raw, why",
    [
        ("", "пустая строка"),
        ("не json вовсе", "не JSON"),
        ("[1, 2, 3]", "не объект"),
        ('{"version": 1}', "не хватает полей"),
    ],
)
def test_malformed_payloads_are_refused(raw, why):
    with pytest.raises(Rejected):
        parse(raw)


def test_a_smuggled_telegram_id_is_refused_not_ignored():
    """Личность из JSON — попытка представиться другим человеком.

    Молча выбросить поле значило бы не заметить нападение: отказ
    обязателен, и он же оставляет след в журнале.
    """
    with pytest.raises(Rejected) as bad:
        parse(payload(telegram_user_id=999))

    assert bad.value.reason == "unexpected_fields"


@pytest.mark.parametrize(
    "field",
    ["employee_id", "organization_id", "office_id", "direction",
     "event_type", "occurred_at"],
)
def test_any_extra_field_refuses_the_whole_message(field):
    with pytest.raises(Rejected):
        parse(payload(**{field: "что угодно"}))


def test_an_unknown_version_is_refused():
    with pytest.raises(Rejected) as bad:
        parse(payload(version=2))
    assert bad.value.reason == "unknown_version"


def test_an_unknown_action_is_refused():
    with pytest.raises(Rejected) as bad:
        parse(payload(action="delete_everything"))
    assert bad.value.reason == "unknown_action"


def test_an_oversized_payload_is_refused():
    """Больше 4096 байт Telegram и сам не доставит."""
    with pytest.raises(Rejected) as bad:
        parse(payload(qr="x" * 5000))
    assert bad.value.reason == "too_large"


@pytest.mark.parametrize(
    "location",
    [
        {"latitude": 900, "longitude": 0, "accuracy": 10},
        {"latitude": 0, "longitude": 900, "accuracy": 10},
        {"latitude": 38.5, "longitude": 68.7, "accuracy": 0},
        {"latitude": 38.5, "longitude": 68.7, "accuracy": -5},
        {"latitude": "38.5", "longitude": 68.7, "accuracy": 10},
        {"latitude": True, "longitude": 68.7, "accuracy": 10},
        {"latitude": 38.5, "longitude": 68.7},
    ],
)
def test_impossible_locations_are_refused(location):
    with pytest.raises(Rejected):
        parse(payload(location=location))


def test_a_blank_qr_is_refused():
    with pytest.raises(Rejected):
        parse(payload(qr="   "))


# --- обработчик -------------------------------------------------------------

def test_the_telegram_id_comes_only_from_the_message():
    """Ни одного пути, которым клиент мог бы назвать другого человека."""
    message = FakeMessage(payload(), user_id=4242)
    client = run(message)

    assert len(client.calls) == 1
    assert client.calls[0]["telegram_user_id"] == 4242


def test_the_backend_is_called_exactly_once():
    client = run(FakeMessage(payload()))

    assert len(client.calls) == 1


def test_only_allowed_fields_reach_the_backend():
    client = run(FakeMessage(payload()))

    assert set(client.calls[0]) == {
        "telegram_user_id", "token", "client_event_id",
        "latitude", "longitude", "accuracy_m",
    }


def test_the_attempt_id_is_passed_through_unchanged():
    """Ключ повтора создаёт клиент, и сервер по нему узнаёт ту же попытку."""
    client = run(FakeMessage(payload(client_event_id="одна-и-та-же")))

    assert client.calls[0]["client_event_id"] == "одна-и-та-же"


def test_a_group_chat_is_not_served():
    """`web_app_data` из группы обрабатывать нечего и незачем.

    Кнопка нижней клавиатуры с `web_app` в группах Telegram и не
    показывается — а обработчик всё равно ограничен личным чатом.
    """
    from aiogram.enums import ChatType
    from aiogram import F

    handler_filter = F.chat.type == ChatType.PRIVATE
    group = FakeMessage(payload(), chat_type="group")

    assert handler_filter.resolve(group) is False


def test_a_message_without_a_sender_is_ignored():
    message = FakeMessage(payload())
    message.from_user = None

    client = run(message)

    assert client.calls == []
    assert message.answers == []


def test_a_broken_payload_answers_instead_of_crashing():
    message = FakeMessage("совсем не json")
    client = run(message)

    assert client.calls == []
    assert message.answers[-1][0] == text.PAYLOAD_REJECTED


# --- ответы человеку --------------------------------------------------------

def test_entry_is_reported_with_the_office():
    message = FakeMessage(payload())
    run(message)

    body = message.answers[-1][0]
    assert "Приход отмечен" in body
    assert "Ташкентский офис" in body


def test_exit_reports_the_hours_from_the_server():
    message = FakeMessage(payload())
    run(message, FakeClient(result={
        "status": "EXITED", "accepted": True,
        "office_name": "Ташкентский офис", "point_name": None,
        "occurred_at": "2026-09-05T13:07:00Z",
        "session": {"duration_seconds": 8 * 3600 + 24 * 60, "status": "CLOSED"},
    }))

    assert "Уход отмечен" in message.answers[-1][0]
    assert "8 ч 24 мин" in message.answers[-1][0]


@pytest.mark.parametrize(
    "status, expected",
    [
        ("QR_EXPIRED", "устарел"),
        ("QR_ALREADY_USED", "уже использован"),
        ("QR_INVALID", "не распознан"),
        ("QR_POINT_INACTIVE", "выключена"),
        ("OFFICE_NOT_ALLOWED", "не назначен"),
        ("OUTSIDE_GEOFENCE", "далеко"),
        ("LOCATION_TOO_VAGUE", "приблизительно"),
        ("ALREADY_INSIDE", "уже отмечены"),
        ("NOT_INSIDE", "Открытой сессии нет"),
    ],
)
def test_every_refusal_gets_its_own_words(status, expected):
    """«Код устарел» и «слишком далеко» требуют разных действий."""
    message = FakeMessage(payload())
    run(message, FakeClient(result={
        "status": status, "accepted": False,
        "office_name": None, "point_name": None,
        "occurred_at": None, "session": None,
    }))

    assert expected in message.answers[-1][0]


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("not_linked", "не связан"),
        ("pending_confirmation", "не подтверждена"),
        ("revoked", "отключена"),
        ("employee_inactive", "неактивна"),
    ],
)
def test_access_refusals_explain_what_to_do(reason, expected):
    message = FakeMessage(payload())
    run(message, FakeClient(
        raises=ApiError(403, "forbidden", "нет", {"reason": reason})
    ))

    assert expected in message.answers[-1][0]


def test_a_backend_outage_does_not_leave_the_user_without_an_answer():
    message = FakeMessage(payload())
    run(message, FakeClient(raises=ServerError(500, "internal_error", "упало")))

    assert message.answers[-1][0] == text.SCAN_FAILED


def test_the_answer_brings_the_keyboard_back():
    """Mini App закрылся, человек снова в чате — кнопки должны быть под рукой."""
    message = FakeMessage(payload())
    run(message)

    _, markup = message.answers[-1]
    labels = [b.text for row in markup.keyboard for b in row]
    assert "📷 Отметиться" in labels


def test_neither_the_code_nor_the_coordinates_reach_the_chat():
    """По строке кода собирают рабочий код, по координатам — маршрут."""
    message = FakeMessage(payload())
    run(message)

    body = message.answers[-1][0]
    assert "HT1" not in body
    assert "38.56" not in body
    assert "68.78" not in body
