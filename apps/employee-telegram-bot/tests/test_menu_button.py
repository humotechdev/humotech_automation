"""Синяя кнопка «Кабинет»: ставится один раз и не переписывается зря.

Ни одного обращения в Telegram: бот подменён дублёром, который считает
вызовы. Проверяется главное свойство — идемпотентность. Бот
перезапускается при каждом обновлении кода, и кнопка, переписываемая на
том же значении, означала бы лишний запрос к Telegram на каждый старт.
"""

from __future__ import annotations

import asyncio

import pytest
from aiogram.types import MenuButtonCommands, MenuButtonWebApp, WebAppInfo

from src.utils import menu_button as mb


class FakeBot:
    """Дублёр Telegram: помнит текущую кнопку и считает записи."""

    def __init__(self, current=None, fails: bool = False):
        self.current = current or MenuButtonCommands()
        self.writes: list[MenuButtonWebApp] = []
        self.fails = fails

    async def get_chat_menu_button(self, **_):
        if self.fails:
            raise RuntimeError("Telegram недоступен")
        return self.current

    async def set_chat_menu_button(self, *, menu_button, **_):
        self.writes.append(menu_button)
        self.current = menu_button


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def url(monkeypatch):
    address = "https://example-stand.ngrok-free.dev"
    monkeypatch.setattr(mb.settings, "mini_app_url", address, raising=False)
    return address


def test_button_is_set_when_it_points_at_commands(url):
    bot = FakeBot()

    run(mb.ensure_menu_button(bot))

    assert len(bot.writes) == 1
    assert isinstance(bot.writes[0], MenuButtonWebApp)
    assert bot.writes[0].web_app.url == url + "/scan"
    assert bot.writes[0].text == mb.BUTTON_TEXT


def test_second_start_writes_nothing(url):
    """Перезапуск бота не должен трогать Telegram без причины."""
    bot = FakeBot()

    run(mb.ensure_menu_button(bot))
    run(mb.ensure_menu_button(bot))

    assert len(bot.writes) == 1


def test_trailing_slash_is_not_a_change(url):
    """Telegram дописывает «/» к корню — это тот же адрес.

    Сравнение «как есть» считало бы кнопку изменившейся при каждом
    запуске и переписывало бы её вечно.
    """
    bot = FakeBot(
        MenuButtonWebApp(
            text=mb.BUTTON_TEXT, web_app=WebAppInfo(url=url + "/scan/")
        )
    )

    run(mb.ensure_menu_button(bot))

    assert bot.writes == []


def test_different_address_is_rewritten(url):
    bot = FakeBot(
        MenuButtonWebApp(
            text=mb.BUTTON_TEXT,
            web_app=WebAppInfo(url="https://old.example.com"),
        )
    )

    run(mb.ensure_menu_button(bot))

    assert len(bot.writes) == 1
    assert bot.writes[0].web_app.url == url + "/scan"


def test_without_address_nothing_is_touched(monkeypatch):
    monkeypatch.setattr(mb.settings, "mini_app_url", "", raising=False)
    bot = FakeBot()

    run(mb.ensure_menu_button(bot))

    assert bot.writes == []


def test_telegram_failure_does_not_stop_the_bot(url):
    """Кнопка — удобство. Упасть из-за неё значило бы не запустить бота."""
    bot = FakeBot(fails=True)

    run(mb.ensure_menu_button(bot))

    assert bot.writes == []


# --- быстрая отметка --------------------------------------------------------

def test_button_opens_the_scan_route(url):
    """Синяя кнопка ведёт на отметку, а не в кабинет целиком.

    Место у поля ввода одно на бота, и занимает его то, что делают
    чаще: отмечаются дважды в день, кабинет открывают изредка.
    """
    bot = FakeBot()

    run(mb.ensure_menu_button(bot))

    assert bot.writes[0].web_app.url.endswith("/scan")
    assert bot.writes[0].text == "Отметиться"


def test_address_is_built_from_the_setting_not_written_in_code(monkeypatch):
    """Домен приходит из MINI_APP_URL и нигде в коде не повторяется.

    Туннель переезжает, и зашитый адрес означал бы кнопку, ведущую
    в никуда, при живой настройке.
    """
    monkeypatch.setattr(
        mb.settings, "mini_app_url", "https://another-stand.example", raising=False
    )
    bot = FakeBot()

    run(mb.ensure_menu_button(bot))

    assert bot.writes[0].web_app.url == "https://another-stand.example/scan"


def test_no_domain_is_hardcoded_in_the_module():
    import inspect

    text = inspect.getsource(mb)
    # Ищется схема, а не конкретный адрес: так проверка переживёт
    # переезд туннеля и поймает любой вписанный домен.
    code = text.split('"""', 2)[-1]
    assert "https://" not in code
    assert "ngrok" not in code


def test_scan_url_tolerates_a_trailing_slash():
    assert mb.scan_url("https://example.test/") == "https://example.test/scan"
    assert mb.scan_url("https://example.test") == "https://example.test/scan"


def test_restart_does_not_leave_the_old_cabinet_button(url):
    """Прежняя кнопка вела в корень кабинета — её надо переписать."""
    bot = FakeBot(
        MenuButtonWebApp(text="Кабинет", web_app=WebAppInfo(url=url))
    )

    run(mb.ensure_menu_button(bot))

    assert len(bot.writes) == 1
    assert bot.writes[0].web_app.url == url + "/scan"
    assert bot.writes[0].text == "Отметиться"


def test_same_address_but_old_caption_is_rewritten(url):
    """Адрес верный, подпись прежняя — кнопку всё равно надо поправить."""
    bot = FakeBot(
        MenuButtonWebApp(text="Кабинет", web_app=WebAppInfo(url=url + "/scan"))
    )

    run(mb.ensure_menu_button(bot))

    assert len(bot.writes) == 1
    assert bot.writes[0].text == "Отметиться"
