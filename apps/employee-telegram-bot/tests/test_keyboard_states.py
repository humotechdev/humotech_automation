"""Нижняя клавиатура: когда она приходит, когда нет и что в ней.

Жалоба, с которой это написано: «кнопок нет, иногда видна только
Помощь». Проверяется ровно граница между тремя состояниями, которые
раньше сливались в одно:

  доступ есть            -> полное меню;
  доступа точно нет      -> «Помощь» и объяснение;
  backend не ответил     -> объяснение БЕЗ клавиатуры.

Третье и было дефектом: сетевой сбой на секунду отбирал у человека
меню до следующего `/start`, потому что бот присылал «Помощь» — одну
кнопку вместо полного меню.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.api.errors import ApiError, Forbidden, ServerError, Unauthorized
from src.handlers.fallback import anything_else
from src.handlers.menu.router import build_menu, menu, start
from src.keyboards import employee as kb
from src.messages import employee as text
from src.middlewares.employee import REASON_UNAVAILABLE, EmployeeMiddleware

PROFILE = {
    "employee": {"full_name": "Иванов Иван", "employee_number": "EMP-0001"},
    "office": {"name": "Офис MAIN", "timezone": "Asia/Dushanbe"},
    "position": {"name": "Инженер"},
}


class FakeBot:
    async def set_chat_menu_button(self, *, chat_id=None, menu_button=None):
        return True


class FakeMessage:
    def __init__(self, user_id=555, chat_type="private"):
        self.from_user = SimpleNamespace(id=user_id, username="ivan",
                                         language_code="ru")
        self.chat = SimpleNamespace(id=user_id, type=chat_type)
        self.bot = FakeBot()
        self.answers: list[tuple[str, object]] = []

    async def answer(self, body, reply_markup=None, **kwargs):
        self.answers.append((body, reply_markup))
        return SimpleNamespace(message_id=len(self.answers))


def command(args: str | None = None):
    return SimpleNamespace(args=args)


def labels(markup) -> list[str]:
    return [b.text for row in markup.keyboard for b in row]


def web_apps(markup) -> int:
    return sum(1 for row in markup.keyboard for b in row if b.web_app is not None)


@pytest.fixture(autouse=True)
def address(monkeypatch):
    from src.config import settings as module

    monkeypatch.setattr(module.settings, "mini_app_url", "https://mini.example", False)
    monkeypatch.setattr(module.settings, "keyboard_launch_buttons", True, False)


# --- доступ есть -----------------------------------------------------------

def test_active_employee_gets_the_whole_menu_after_a_bot_restart():
    """Клавиатура живёт у человека в Telegram и переживает рестарт бота.

    Но первое же действие после перезапуска должно возвращать полный
    набор: состояние бота не хранится нигде, и «забыл» здесь означало бы
    «отобрал кнопки».
    """
    message = FakeMessage()
    asyncio.run(start(message, PROFILE, None))

    _, markup = message.answers[-1]
    assert kb.BTN_SCAN in labels(markup)
    assert kb.BTN_OPEN in labels(markup)
    assert len(labels(markup)) == len(kb.ALL_BUTTONS) - 1  # всё, кроме запасной «кабинет»


def test_the_keyboard_carries_the_flags_that_keep_it_open():
    markup = build_menu(PROFILE, FakeMessage())

    assert markup.is_persistent is True
    assert markup.resize_keyboard is True
    assert markup.one_time_keyboard is False
    # `selective` сузил бы показ до отдельных участников — в личном чате
    # это лишнее, и случайно выставленный он выглядел бы как пропажа.
    assert markup.selective is None


def test_the_two_launch_buttons_open_the_mini_app():
    markup = build_menu(PROFILE, FakeMessage())
    row = markup.keyboard[0]

    assert [b.text for b in row] == [kb.BTN_SCAN, kb.BTN_OPEN]
    assert row[0].web_app.url.endswith("/scan?source=keyboard")
    assert row[1].web_app.url.endswith("/")


def test_menu_command_sends_a_new_message_with_the_keyboard():
    # Нижнюю клавиатуру нельзя обновить редактированием: она приходит
    # только с новым сообщением.
    message = FakeMessage()
    asyncio.run(menu(message, command(), PROFILE, None))

    body, markup = message.answers[-1]
    assert body == "Меню"
    assert web_apps(markup) == 2


# --- клиент не показал кнопки запуска --------------------------------------

def test_plain_menu_keeps_the_same_labels_without_web_app():
    """Тот же набор без `web_app` — и как проверка клиента, и как выход.

    Если этот вариант виден, а обычный нет, дело в клиенте и его
    отношении к `web_app` в нижней клавиатуре, а не в том, что бот
    ничего не прислал.
    """
    message = FakeMessage()
    asyncio.run(menu(message, command("текст"), PROFILE, None))

    _, markup = message.answers[-1]
    assert labels(markup)[:2] == [kb.BTN_SCAN, kb.BTN_OPEN]
    assert web_apps(markup) == 0
    assert len(labels(markup)) == len(kb.ALL_BUTTONS) - 1  # всё, кроме запасной «кабинет»


def test_the_switch_turns_the_launch_row_into_plain_buttons(monkeypatch):
    from src.config import settings as module

    monkeypatch.setattr(module.settings, "keyboard_launch_buttons", False, False)
    markup = build_menu(PROFILE, FakeMessage())

    assert labels(markup)[:2] == [kb.BTN_SCAN, kb.BTN_OPEN]
    assert web_apps(markup) == 0


# --- backend не ответил ----------------------------------------------------

def test_a_backend_outage_does_not_take_the_keyboard_away():
    """Главная проверка. Состояние неизвестно — клавиатуру не трогаем."""
    message = FakeMessage()
    asyncio.run(start(message, None, REASON_UNAVAILABLE))

    body, markup = message.answers[-1]
    assert body == text.BACKEND_DOWN
    assert markup is None


def test_a_backend_outage_is_not_reported_as_missing_link():
    message = FakeMessage()
    asyncio.run(anything_else(message, None, REASON_UNAVAILABLE))

    body, markup = message.answers[-1]
    assert body == text.BACKEND_DOWN
    assert text.NOT_LINKED not in body
    assert markup is None


def test_menu_command_during_an_outage_leaves_the_keyboard_alone():
    message = FakeMessage()
    asyncio.run(menu(message, command(), None, REASON_UNAVAILABLE))

    assert message.answers[-1][1] is None


# --- доступа действительно нет ---------------------------------------------

def test_an_unlinked_person_gets_help_and_an_explanation():
    message = FakeMessage()
    asyncio.run(start(message, None, "not_linked"))

    body, markup = message.answers[-1]
    assert body == text.NOT_LINKED
    assert labels(markup) == [kb.BTN_HELP]


def test_a_pending_link_is_told_to_wait_not_to_ask_for_a_new_one():
    message = FakeMessage()
    asyncio.run(start(message, None, "pending_confirmation"))

    assert message.answers[-1][0] == text.PENDING


def test_a_confirmed_link_restores_the_full_menu():
    """После подтверждения HR первое же действие возвращает все кнопки."""
    message = FakeMessage()
    asyncio.run(start(message, None, "pending_confirmation"))
    assert labels(message.answers[-1][1]) == [kb.BTN_HELP]

    asyncio.run(start(message, PROFILE, None))
    assert len(labels(message.answers[-1][1])) == len(kb.ALL_BUTTONS) - 1  # всё, кроме запасной «кабинет»


# --- следующие ответы не затирают меню -------------------------------------

def test_an_unrecognised_message_returns_the_full_menu_not_help():
    message = FakeMessage()
    asyncio.run(anything_else(message, PROFILE, None))

    _, markup = message.answers[-1]
    assert len(labels(markup)) == len(kb.ALL_BUTTONS) - 1  # всё, кроме запасной «кабинет»
    assert web_apps(markup) == 2


# --- разбор ответа backend -------------------------------------------------

def _resolve(exception=None, profile=PROFILE):
    """Прогоняет middleware и возвращает то, что она положила в `data`."""

    class Client:
        async def profile(self, telegram_id):
            if exception is not None:
                raise exception
            return profile

    data: dict = {"event_from_user": SimpleNamespace(id=555)}
    seen: dict = {}

    async def handler(event, payload):
        seen.update(payload)

    asyncio.run(EmployeeMiddleware(Client())(handler, SimpleNamespace(), data))
    return seen


@pytest.mark.parametrize(
    "error, expected",
    [
        (Unauthorized(401, "authentication_failed", "нет", {"reason": "not_linked"}),
         "not_linked"),
        (Unauthorized(401, "authentication_failed", "нет",
                      {"reason": "pending_confirmation"}), "pending_confirmation"),
        # 403 раньше падал в общую ветку и доезжал как «сервер недоступен»:
        # человек с отозванной привязкой видел не ту причину.
        (Forbidden(403, "forbidden", "нет", {"reason": "revoked"}), "revoked"),
    ],
)
def test_a_refusal_keeps_its_reason(error, expected):
    assert _resolve(error)["denial"] == expected
    assert _resolve(error)["employee"] is None


@pytest.mark.parametrize(
    "error",
    [ServerError(500, "internal_error", "упал"), ApiError(0, "network", "нет сети")],
)
def test_a_network_failure_is_not_a_refusal(error):
    assert _resolve(error)["denial"] == REASON_UNAVAILABLE


def test_a_working_backend_returns_the_profile():
    assert _resolve()["employee"] == PROFILE
    assert _resolve()["denial"] is None
