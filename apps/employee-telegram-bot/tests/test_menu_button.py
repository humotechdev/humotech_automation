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
    assert bot.writes[0].web_app.url == url
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
            text=mb.BUTTON_TEXT, web_app=WebAppInfo(url=url + "/")
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
    assert bot.writes[0].web_app.url == url


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
