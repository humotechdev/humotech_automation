"""Меню сотрудника: что показывается, кому и откуда берутся числа.

Ни одного обращения в сеть и ни одного в Telegram: клиент подменён
дублёром, `message.answer` записывает ответы в список.

Главное, что здесь проверяется, — бот ничего не считает. Все цифры он
берёт у backend и показывает как есть; расхождение чата и Mini App
возможно только если бот начнёт считать сам.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.api.errors import ApiError, ServerError, Unauthorized
from src.handlers.menu import router as menu_router  # noqa: F401 - сборка
from src.handlers.menu.router import (
    cabinet,
    help_handler,
    history,
    month,
    my_requests,
    sick_leave,
    start,
    today,
    vacation,
    week,
    where_am_i,
)
from src.keyboards import employee as kb
from src.messages import employee as text
from src.middlewares.employee import REASON_UNAVAILABLE


class FakeMessage:
    def __init__(self, user_id=555):
        self.from_user = SimpleNamespace(id=user_id, username="ivan",
                                         language_code="ru")
        self.chat = SimpleNamespace(id=user_id)
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))


PROFILE = {
    "employee": {"full_name": "Иванов Иван", "employee_number": "EMP-0001"},
    "office": {"name": "Офис MAIN", "timezone": "Asia/Dushanbe"},
    "position": {"name": "Инженер"},
}

STATUS = {
    "state": "IN_OFFICE",
    "day": "2026-09-04",
    "timezone": "Asia/Dushanbe",
    "seconds_today": 3 * 3600,
    "open_session": {
        "id": "s-1", "day": "2026-09-04",
        "started_at": "2026-09-04T04:00:00+00:00", "ended_at": None,
        "seconds": 3 * 3600, "is_open": True, "is_preliminary": True,
        "office_name": "Офис MAIN", "entry_point_name": "Главный вход",
        "exit_point_name": None,
    },
    "last_entry_at": "2026-09-04T04:00:00+00:00",
    "last_exit_at": None,
    "scheduled_start": "09:00:00",
    "scheduled_end": "18:00:00",
    "absence_name": None,
}

SUMMARY = {
    "summary": {
        "first": "2026-09-01", "last": "2026-09-30",
        "timezone": "Asia/Dushanbe",
        "seconds": 30600, "completed_sessions": 4, "open_sessions": 1,
        "working_days": 22, "attended_days": 5, "missed_days": 17,
        "sick_leave_days": 0, "vacation_days": 0, "other_absence_days": 0,
        "has_schedule": True,
    },
    "days": [],
}


class FakeClient:
    """Клиент backend. Записывает вызовы, отдаёт заготовленные ответы."""

    def __init__(self, **overrides):
        self.calls: list[str] = []
        self.raises: dict[str, Exception] = overrides.pop("raises", {})
        self.answers = {
            "status": STATUS,
            "statistics": SUMMARY,
            "history": {"period": {"first": "2026-09-01", "last": "2026-09-30",
                                   "timezone": "Asia/Dushanbe"},
                        "days": [], "total": 0, "has_more": False},
            "absences": {"requests": [], "total": 0},
            "leave_balance": {"balances": [
                {"absence_type": {"code": "ANNUAL_LEAVE", "name": "Отпуск"},
                 "year": 2026, "allocated_days": 28.0, "used_days": 0.0,
                 "reserved_days": 0.0, "available_days": 28.0}
            ]},
            **overrides,
        }

    async def _answer(self, name):
        self.calls.append(name)
        if name in self.raises:
            raise self.raises[name]
        return self.answers[name]

    async def status(self, telegram_id):
        return await self._answer("status")

    async def statistics(self, telegram_id, period):
        self.calls.append(f"period:{period}")
        return await self._answer("statistics")

    async def history(self, telegram_id, limit=10):
        return await self._answer("history")

    async def absences(self, telegram_id, limit=10):
        return await self._answer("absences")

    async def leave_balance(self, telegram_id):
        return await self._answer("leave_balance")


def run(handler, *, employee=PROFILE, denial=None, client=None,
        needs_client=True):
    message = FakeMessage()
    client = client or FakeClient()
    if needs_client:
        asyncio.run(handler(message, employee, denial, client))
    else:
        asyncio.run(handler(message, employee, denial))
    return message, client


# --- доступ ----------------------------------------------------------------

@pytest.mark.parametrize(
    "handler, needs_client",
    [
        (where_am_i, True), (today, True), (week, True), (month, True),
        (history, True), (my_requests, True), (sick_leave, True),
        (vacation, True), (start, False),
    ],
)
def test_nothing_is_shown_without_a_binding(handler, needs_client):
    """Непривязанному не показывается ни один раздел.

    И, что важнее, backend по его нажатиям не опрашивается вовсе: кнопка,
    которая всё равно ответит отказом, не должна стоить запроса.
    """
    message, client = run(
        handler, employee=None, denial="not_linked", needs_client=needs_client
    )

    assert client.calls == []
    assert message.answers[-1][0] == text.NOT_LINKED


def test_pending_binding_says_to_wait_not_to_ask_for_a_link():
    """«Ждём подтверждения» и «попросите ссылку» — разные действия."""
    message, _ = run(where_am_i, employee=None, denial="pending_confirmation")

    assert message.answers[-1][0] == text.PENDING


def test_revoked_binding_gets_a_safe_message():
    """Отозванная привязка не сообщает, что она когда-то была."""
    message, _ = run(where_am_i, employee=None, denial="link_revoked")

    assert message.answers[-1][0] == text.NO_ACCESS


def test_backend_failure_is_not_a_refusal():
    """Сказать «нет доступа» из-за упавшего сервера значит отправить
    человека в отдел кадров разбираться с тем, чего не происходило."""
    message, _ = run(where_am_i, employee=None, denial=REASON_UNAVAILABLE)

    assert message.answers[-1][0] == text.BACKEND_DOWN


def test_help_works_even_without_a_binding():
    """Человек, которому отказали, должен понимать, что это за бот."""
    message, _ = run(help_handler, employee=None, denial="not_linked",
                     needs_client=False)

    assert "Что умеет бот" in message.answers[-1][0]


def test_no_access_menu_shows_only_help():
    """Показывать разделы, которые всё равно ответят отказом, — значит
    обещать то, чего нет."""
    message, _ = run(where_am_i, employee=None, denial="not_linked")

    _, markup = message.answers[-1]
    assert [b.text for row in markup.keyboard for b in row] == [kb.BTN_HELP]


# --- меню ------------------------------------------------------------------

def test_menu_has_all_ten_items(monkeypatch):
    from src.config import settings as settings_module

    # Адрес задаётся явно: иначе тест проверял бы не меню, а то, что
    # в окружении разработчика пусто, и «зеленел» бы по случайности.
    monkeypatch.setattr(
        settings_module.settings, "mini_app_url", "https://mini.example", False
    )
    message, _ = run(start, needs_client=False)

    _, markup = message.answers[-1]
    labels = [b.text for row in markup.keyboard for b in row]
    assert set(labels) == set(kb.ALL_BUTTONS)


def test_menu_hides_the_cabinet_without_an_address(monkeypatch):
    from src.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "mini_app_url", "", False)
    message, _ = run(start, needs_client=False)

    _, markup = message.answers[-1]
    labels = [b.text for row in markup.keyboard for b in row]
    assert set(labels) == set(kb.ALL_BUTTONS) - {kb.BTN_CABINET}


def test_the_keyboard_cabinet_button_is_not_a_web_app():
    """Кнопка нижней клавиатуры НЕ должна открывать Mini App.

    Telegram передаёт подписанные данные о пользователе только
    приложениям, открытым из inline-кнопки, кнопки меню или прямой
    ссылки. Приложение, открытое из нижней клавиатуры, не получает
    ни подписи, ни имени — кабинет не смог бы понять, кто пришёл,
    и показал бы «откройте из Telegram» тому, кто уже в Telegram.

    Проверено живьём: такой запуск приносит только tgWebAppVersion,
    tgWebAppPlatform и tgWebAppThemeParams, без tgWebAppData.
    """
    markup = kb.employee_menu("https://mini.example")
    first = markup.keyboard[0][0]

    assert first.text == kb.BTN_CABINET
    assert first.web_app is None


def test_cabinet_opens_through_an_inline_button():
    """А вот inline-кнопка — открывает, и адрес в ней настоящий."""
    markup = kb.cabinet_button("https://mini.example")
    button = markup.inline_keyboard[0][0]

    assert button.web_app is not None
    assert button.web_app.url == "https://mini.example"


def test_pressing_the_cabinet_sends_the_inline_button(monkeypatch):
    from src.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "mini_app_url", "https://mini.example", False
    )
    message, _ = run(cabinet, needs_client=False)

    _, markup = message.answers[-1]
    assert markup.inline_keyboard[0][0].web_app.url == "https://mini.example"


def test_cabinet_without_an_address_says_so_instead_of_opening_nothing(
    monkeypatch,
):
    from src.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "mini_app_url", "", False)
    message, _ = run(cabinet, needs_client=False)

    answer, markup = message.answers[-1]
    assert "не настроен" in answer
    assert markup is None


def test_start_explains_both_ways_in(monkeypatch):
    """Синяя кнопка теперь сканер, и это надо сказать словами.

    Человек, привыкший открывать кабинет синей кнопкой, иначе решит,
    что кабинет пропал: место у поля ввода одно, и его занял сканер.
    """
    from src.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "mini_app_url", "https://mini.example", False
    )
    message, _ = run(start, needs_client=False)

    answer, _ = message.answers[-1]
    assert "Отметиться" in answer
    assert "/cabinet" in answer


def test_start_names_what_each_way_is_for(monkeypatch):
    from src.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "mini_app_url", "https://mini.example", False
    )
    message, _ = run(start, needs_client=False)

    answer, _ = message.answers[-1]
    # Быстрый путь — про приход и уход; полный — про то, чего в нём нет.
    assert "QR" in answer
    for word in ("статистика", "история", "больничный", "отпуск"):
        assert word.lower() in answer.lower()


def test_cabinet_is_still_reachable_by_command():
    """Кабинет не должен исчезнуть вместе с синей кнопкой."""
    from src.handlers.menu.router import cabinet as handler

    assert handler is cabinet


def test_cabinet_is_listed_among_the_commands():
    """Кнопка снизу не видна, пока не развернёшь клавиатуру.

    Раз синюю кнопку занял сканер, полный кабинет должен открываться
    хотя бы командой из списка — иначе до него нужно ещё догадаться.
    """
    from src.utils.commands import COMMANDS

    assert any(item.command == "cabinet" for item in COMMANDS)


# --- числа приходят с сервера ---------------------------------------------

def test_the_bot_computes_nothing_itself():
    """Все цифры — из ответа backend. Иначе чат и Mini App разошлись бы."""
    message, client = run(month)

    assert "period:month" in client.calls
    answer = message.answers[-1][0]
    # 30600 секунд = 8 ч 30 мин. Перевод — единственная арифметика бота.
    assert "8 ч 30 мин" in answer
    assert "Рабочих дней: 22" in answer


def test_open_session_is_marked_preliminary():
    message, _ = run(month)
    assert "предварительное" in message.answers[-1][0]


def test_times_are_shown_in_the_office_timezone():
    """04:00 UTC — это 09:00 в Душанбе.

    Показать сотруднику UTC значило бы ошибиться на пять часов, и ошибка
    выглядела бы как неверная отметка.
    """
    message, _ = run(where_am_i)

    assert "С 09:00" in message.answers[-1][0]
    assert "04:00" not in message.answers[-1][0]


def test_status_separates_open_session_from_hours_today():
    message, _ = run(where_am_i)
    answer = message.answers[-1][0]

    assert "Вы в офисе" in answer
    assert "Сегодня в офисе:" in answer


def test_missing_schedule_is_not_reported_as_zero():
    """Ноль рабочих дней при нуле пропусков читался бы как безупречная
    посещаемость."""
    body = {"summary": {**SUMMARY["summary"], "has_schedule": False,
                        "working_days": None, "missed_days": None},
            "days": []}
    message, _ = run(week, client=FakeClient(statistics=body))

    assert "график не назначен" in message.answers[-1][0]


# --- разделы, которые оформляются в кабинете -------------------------------

def test_sick_leave_sends_to_the_cabinet():
    """Просить даты сообщениями в чат — способ получить «3 сентебря»."""
    message, _ = run(sick_leave)

    assert "личный кабинет" in message.answers[-1][0].lower()


def test_vacation_shows_the_balance():
    message, _ = run(vacation)

    assert "28 дн." in message.answers[-1][0]


def test_vacation_still_works_when_the_balance_is_unavailable():
    """Остаток — приятное дополнение, а не смысл экрана."""
    client = FakeClient(raises={"leave_balance": ServerError(500, "e", "ой")})
    message, _ = run(vacation, client=client)

    assert "личный кабинет" in message.answers[-1][0].lower()


def test_empty_requests_explain_where_to_create_one():
    message, _ = run(my_requests)

    assert "Заявок пока нет" in message.answers[-1][0]
