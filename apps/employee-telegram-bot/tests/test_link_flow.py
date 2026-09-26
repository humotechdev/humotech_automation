"""Привязка по одноразовой ссылке со стороны бота.

Проверяется ровно то, за что бот отвечает: разбор полезной нагрузки `/start`,
один вызов backend и подбор текста под КОД ответа, а не под его формулировку.
Решения (срок, одноразовость, отзыв, чей это сотрудник) принимает backend —
здесь их нет и быть не должно.

Ни одного обращения в Telegram и ни одного в сеть: клиент backend подменён
дублёром, а `message.answer` записывает ответы в список.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.api.errors import ApiError, Conflict, ServerError
from src.handlers.start.router import parse_link_payload, start_with_link
from src.messages import link as start_module


# --- разбор полезной нагрузки ---------------------------------------------

@pytest.mark.parametrize(
    "payload, expected",
    [
        ("link_AbCdEf-123_456", "AbCdEf-123_456"),
        ("  link_token  ", "token"),
        ("link_", None),
        ("", None),
        (None, None),
        # Посторонняя нагрузка не должна оказаться «почти токеном»
        # и уйти на backend.
        ("ref_partner42", None),
        ("linkAbCd", None),
        ("LINK_AbCd", None),
    ],
)
def test_payload_parsing(payload, expected):
    assert parse_link_payload(payload) == expected


# --- дублёры ---------------------------------------------------------------

def _remember_menu_write(message):
    async def write(*, chat_id=None, menu_button=None):
        message.menu_writes.append((chat_id, menu_button))

    return write


class FakeMessage:
    """Сообщение, которое запоминает ответы вместо отправки в Telegram."""

    def __init__(self, user_id=555, username="ivan", language_code="ru"):
        self.from_user = SimpleNamespace(
            id=user_id, username=username, language_code=language_code
        )
        self.chat = SimpleNamespace(id=user_id)
        # Обычный /start снимает персональную кнопку чата, а чужая
        # полезная нагрузка ведёт себя как обычный /start.
        self.bot = SimpleNamespace(
            set_chat_menu_button=_remember_menu_write(self)
        )
        self.menu_writes: list[tuple[object, object]] = []
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))


class FakeState:
    def __init__(self):
        self.cleared = False

    async def clear(self):
        self.cleared = True


class FakeClient:
    """Клиент backend. Либо запоминает вызов, либо бросает заданную ошибку."""

    def __init__(self, *, raises: Exception | None = None,
                 recognizes: Exception | dict | None = None):
        self.raises = raises
        self.recognizes = recognizes
        self.calls: list[dict] = []
        self.recognized: list[dict] = []

    async def consume_link_token(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return {"status": "PENDING", "employee_known": True}

    async def recognize(self, **kwargs):
        self.recognized.append(kwargs)
        if isinstance(self.recognizes, Exception):
            raise self.recognizes
        return self.recognizes or {
            "status": "PENDING",
            "full_name": "Мурадов Азизбек",
            "employment_status": "PROBATION",
            "hire_date": "2026-09-18",
            "office_name": "город Ташкент",
            "department_name": None,
            "position_name": None,
            "schedule_name": "Пн–Пт 09:00–18:00",
            "manager_name": None,
        }


def run_start(payload: str, client: FakeClient, message=None) -> FakeMessage:
    message = message or FakeMessage()
    asyncio.run(
        start_with_link(
            message,
            SimpleNamespace(command="start", args=payload, mention=None),
            FakeState(),
            client=client,
            employee=None,
            denial="not_linked",
        )
    )
    return message


def refusal(reason: str) -> Conflict:
    return Conflict(409, "conflict", "неважно что тут написано", {"reason": reason})


# --- успешный переход ------------------------------------------------------

def test_successful_click_shows_terms_before_access():
    """После ссылки сотрудник сперва принимает условия, доступа пока нет."""
    client = FakeClient()
    message = run_start("link_the-token", client)

    text, markup = message.answers[-1]
    assert text == start_module.LINK_PENDING
    # Нужна только inline-кнопка согласия; обычное меню ещё рано.
    assert markup is not None
    assert markup.inline_keyboard[0][0].callback_data == "link:accept"


def test_bot_sends_only_what_telegram_confirmed():
    """Сотрудника и организацию бот не передаёт: их определяет токен.

    Всё, что уходит на backend, — токен из ссылки и то, что подтвердил
    сам Telegram.
    """
    client = FakeClient()
    run_start("link_the-token", client)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call == {
        "token": "the-token",
        "telegram_user_id": 555,
        "telegram_chat_id": 555,
        "telegram_username": "ivan",
        "language_code": "ru",
    }
    assert "employee_id" not in call
    assert "organization_id" not in call


# --- отказы ----------------------------------------------------------------

@pytest.mark.parametrize(
    "reason, expected",
    [
        ("expired", start_module.LINK_EXPIRED),
        ("revoked", start_module.LINK_REVOKED),
        ("used", start_module.LINK_USED),
        ("invalid", start_module.LINK_INVALID),
        ("telegram_taken", start_module.LINK_TAKEN),
        ("employee_inactive", start_module.LINK_INACTIVE),
    ],
)
def test_every_refusal_gets_its_own_message(reason, expected):
    """Человеку надо сказать разное: «попросите новую» и «уже использована» —
    это разные действия с его стороны."""
    message = run_start("link_x", FakeClient(raises=refusal(reason)))
    assert message.answers[-1][0] == expected


def test_unknown_reason_falls_back_to_a_neutral_message():
    """Backend может завести новый код раньше, чем бот о нём узнает.
    Молчание в этом случае хуже общего текста."""
    message = run_start("link_x", FakeClient(raises=refusal("что-то новое")))
    assert message.answers[-1][0] == start_module.LINK_ERROR


def test_pending_link_can_resume_with_terms_after_a_restart():
    """Старый переход не должен оставлять человека ждать HR навсегда."""
    message = run_start("link_x", FakeClient(raises=refusal("pending")))
    said, markup = message.answers[-1]
    assert said == start_module.LINK_PENDING
    assert markup.inline_keyboard[0][0].callback_data == "link:accept"


def test_backend_outage_does_not_leave_the_user_without_an_answer():
    message = run_start(
        "link_x", FakeClient(raises=ServerError(500, "internal_error", "упало"))
    )
    assert message.answers[-1][0] == start_module.LINK_ERROR


def test_error_without_details_does_not_crash():
    """`details` может не прийти вовсе — например, от промежуточного прокси."""
    message = run_start(
        "link_x", FakeClient(raises=ApiError(409, "conflict", "без подробностей"))
    )
    assert message.answers[-1][0] == start_module.LINK_ERROR


def test_refusal_never_shows_the_employee_keyboard():
    """Клавиатура сотрудника у непривязанного человека — обещание доступа,
    которого нет."""
    for reason in ("expired", "revoked", "used", "invalid", "telegram_taken"):
        message = run_start("link_x", FakeClient(raises=refusal(reason)))
        markup = message.answers[-1][1]
        assert markup.__class__.__name__ == "ReplyKeyboardMarkup", reason


# --- посторонняя нагрузка --------------------------------------------------

def test_foreign_payload_does_not_consume_a_token():
    """Нагрузка не наша — ссылку не гасим.

    Но человека, у которого привязки нет, пробуем узнать: бот не может
    написать первым, и первый запуск — единственный момент, когда это
    вообще возможно.
    """
    client = FakeClient()
    message = run_start("ref_partner42", client)

    assert client.calls == []
    assert len(client.recognized) == 1
    assert message.answers[-1][0].startswith("Добро пожаловать в HUMOTECH")


def test_recognized_employee_is_greeted_with_the_facts():
    """Узнанному сразу говорят, куда он принят и по какому графику."""
    client = FakeClient()
    message = run_start("ref_partner42", client)

    said = message.answers[-1][0]
    assert "Мурадов Азизбек" in said
    assert "на стажировку" in said
    assert "город Ташкент" in said
    assert "Пн–Пт 09:00–18:00" in said
    # Доступа это ещё не даёт, и об этом сказано прямо.
    assert "подтвердит привязку" in said


def test_unknown_person_is_sent_to_hr():
    """Постороннему не намекают, что такой сотрудник есть."""
    client = FakeClient(recognizes=refusal("unknown"))
    message = run_start("ref_partner42", client)

    said = message.answers[-1][0]
    assert said.startswith("Мы вас пока не ждём")
    assert "Мурадов" not in said


# --- секреты ---------------------------------------------------------------

def test_token_never_appears_in_what_the_user_sees():
    """Токен рабочий, пока ссылка жива: эхо его в чат — это ещё одна копия
    секрета в переписке."""
    token = "very-secret-token-value"
    for client in (FakeClient(), FakeClient(raises=refusal("expired"))):
        message = run_start(f"link_{token}", client)
        assert all(token not in text for text, _ in message.answers)


def test_token_is_not_logged(caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="src.handlers.start.router")
    token = "another-secret-token"
    run_start(f"link_{token}", FakeClient(raises=refusal("revoked")))

    assert token not in caplog.text
    # Причина отказа в журнале нужна: без неё разбираться не с чем.
    assert "revoked" in caplog.text


# --- шов с backend --------------------------------------------------------
#
# Дублёр выше проверяет handler, но не то, ЧТО уходит по проводу. Ниже —
# настоящий `LiveBackendClient` с подменённой сессией aiohttp: путь, заголовок
# и разбор ответа. Ровно эти три вещи невозможно проверить ни со стороны
# бота, ни со стороны backend по отдельности — а первый настоящий переход
# по ссылке сломается именно здесь.

class FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class RecordingSession:
    """Сессия aiohttp, которая ничего не отправляет, а записывает вызов."""

    def __init__(self, response: FakeResponse):
        self._response = response
        self.recorded: dict | None = None
        self.closed = False

    def request(self, method, url, json=None, params=None, headers=None):
        self.recorded = {
            "method": method, "url": url, "json": json,
            "params": params, "headers": headers,
        }
        return self._response

    async def close(self):
        self.closed = True


def _live_client(response: FakeResponse):
    from src.api.selfservice import SelfServiceClient

    client = SelfServiceClient(base_url="http://backend:8000/api/v1")
    session = RecordingSession(response)

    async def _get_session():
        return session

    client._get_session = _get_session
    return client, session


def test_live_client_hits_the_documented_path_with_the_bot_secret(monkeypatch):
    """Путь и заголовок зафиксированы: смена префикса обязана ломать тест,
    а не первый живой переход по ссылке."""
    from src.config.settings import settings

    monkeypatch.setattr(settings, "backend_bot_secret", "секрет-бота", raising=False)
    client, session = _live_client(
        FakeResponse(201, {"status": "PENDING", "employee_known": True})
    )

    result = asyncio.run(
        client.consume_link_token(
            token="the-token", telegram_user_id=555, telegram_chat_id=555,
            telegram_username="ivan", language_code="ru",
        )
    )

    assert result == {"status": "PENDING", "employee_known": True}
    assert session.recorded["method"] == "POST"
    assert session.recorded["url"] == "http://backend:8000/api/v1/telegram/bot/link"
    assert session.recorded["headers"]["X-Bot-Token"] == "секрет-бота"
    # Токена пользователя здесь нет: за этим запросом человека нет вовсе.
    assert "Authorization" not in session.recorded["headers"]
    assert session.recorded["json"] == {
        "token": "the-token",
        "telegram_user_id": 555,
        "telegram_chat_id": 555,
        "telegram_username": "ivan",
        "language_code": "ru",
    }


def test_live_client_reads_the_reason_out_of_the_backend_error_body():
    """Форма тела ошибки — контракт между двумя приложениями.

    Backend отвечает `{"error": {"code", "message", "details"}}`, и причина
    лежит в `details.reason`. Handler выбирает по ней текст; ошибка в разборе
    превратила бы любой понятный отказ в «сервис недоступен».
    """
    client, _ = _live_client(
        FakeResponse(
            409,
            {
                "error": {
                    "code": "conflict",
                    "message": "Срок действия ссылки истёк",
                    "details": {"reason": "expired"},
                }
            },
        )
    )

    with pytest.raises(ApiError) as exc:
        asyncio.run(
            client.consume_link_token(
                token="x", telegram_user_id=1, telegram_chat_id=1
            )
        )

    assert exc.value.status == 409
    assert exc.value.details == {"reason": "expired"}
    # И этот же путь дальше — до текста, который увидит человек.
    assert (
        start_module.LINK_MESSAGES[exc.value.details["reason"]]
        == start_module.LINK_EXPIRED
    )
