"""Синяя кнопка рядом с полем ввода.

Единственный вход в кабинет, который не устаревает. Inline-кнопка живёт
внутри сообщения: Telegram вшивает в неё адрес навсегда, и после смены
адреса старое сообщение ведёт в никуда — с виду рабочая кнопка отвечает
`ERR_NAME_NOT_RESOLVED`, потому что имени мёртвого туннеля больше нет
в DNS. Кнопка меню хранится на стороне Telegram в одном экземпляре,
и переустановить её — значит починить сразу все чаты.

Ставится идемпотентно: сначала спрашивается текущее значение, и запрос
на запись уходит, только если адрес или подпись действительно другие.
Бот перезапускается при каждом обновлении кода, и переписывать кнопку
на том же самом значении означало бы дёргать Telegram без повода.

Плата за это — список команд перестаёт открываться синей кнопкой:
у неё одно значение на бота. Команды остаются доступны по вводу «/»
и в подсказке Telegram, а кабинет важнее списка, который дублирует
нижнюю клавиатуру.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import MenuButtonWebApp, WebAppInfo

from src.config.settings import settings

logger = logging.getLogger(__name__)

#: Подпись кнопки. Короткая намеренно: Telegram обрезает длинную, и
#: обрезанная подпись выглядит как ошибка вёрстки.
BUTTON_TEXT = "Кабинет"


def _same_address(left: str | None, right: str | None) -> bool:
    """Сравнение адресов без учёта хвостовой косой черты.

    Telegram возвращает адрес нормализованным и дописывает `/` к корню.
    Сравнение «как есть» считало бы кнопку изменившейся при каждом
    запуске и переписывало бы её вечно.
    """
    if left is None or right is None:
        return False
    return left.rstrip("/") == right.rstrip("/")


async def ensure_menu_button(bot: Bot) -> None:
    """Проверить кнопку меню и поправить, если она смотрит не туда."""
    url = settings.mini_app_url
    if not url:
        # Кнопка, которая ничего не открывает, хуже её отсутствия —
        # то же правило, что и для нижней клавиатуры.
        logger.info("menu button: MINI_APP_URL не задан, кнопка не ставится")
        return

    try:
        current = await bot.get_chat_menu_button()
        if isinstance(current, MenuButtonWebApp) and _same_address(
            current.web_app.url, url
        ) and current.text == BUTTON_TEXT:
            logger.info("menu button: уже указывает на %s", url)
            return

        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text=BUTTON_TEXT, web_app=WebAppInfo(url=url)
            )
        )
        logger.info("menu button: переведена на %s", url)
    except Exception:
        # Кнопка — удобство, а не работоспособность. Упасть на старте
        # из-за неё значило бы не запустить бота целиком.
        logger.exception("menu button: установить не удалось")


__all__ = ["BUTTON_TEXT", "ensure_menu_button"]
