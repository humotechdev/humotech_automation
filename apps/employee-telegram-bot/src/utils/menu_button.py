"""Синяя кнопка рядом с полем ввода.

Открывает кабинет целиком. Отметка живёт не здесь, а на нижней
клавиатуре (`keyboards/employee.py`): там помещаются ДВЕ кнопки, а у
поля ввода место одно на бота, и разменивать его на что-то одно больше
не нужно.

Inline-кнопка для постоянного входа не годится: она
живёт внутри сообщения, Telegram вшивает в неё адрес навсегда, и после
смены адреса старое сообщение ведёт в никуда — с виду рабочая кнопка
отвечает `ERR_NAME_NOT_RESOLVED`, потому что имени мёртвого туннеля
больше нет в DNS. Кнопка меню хранится на стороне Telegram в одном
экземпляре, и переустановить её — значит починить сразу все чаты.

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
from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo

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
    base = settings.mini_app_url
    if not base:
        # Кнопка, которая ничего не открывает, хуже её отсутствия —
        # то же правило, что и для нижней клавиатуры.
        logger.info("menu button: MINI_APP_URL не задан, кнопка не ставится")
        return

    # Корень, а не /scan: синяя кнопка открывает кабинет целиком.
    url = base

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


async def drop_chat_override(bot: Bot | None, chat_id: int) -> None:
    """Вернуть чату общую кнопку бота.

    Кнопку можно задать не только боту целиком, но и отдельному чату,
    и тогда персональная перекрывает общую НАВСЕГДА. Общую при этом
    видно и в логах, и через `getChatMenuButton` без `chat_id` — она
    верная; а человек у себя видит старую и справедливо считает, что
    ничего не поменялось. Поймано ровно так: бот записал `/scan`,
    Telegram подтвердил, а два чата продолжали показывать «Кабинет».

    Пишется без чтения: `getChatMenuButton` с `chat_id` возвращает
    ДЕЙСТВУЮЩУЮ кнопку, а не признак того, что она персональная, и
    отличить «своя такая же» от «унаследована общая» по ответу нельзя.
    Запись `default` в обоих случаях означает одно и то же и ничего
    не портит.

    Вызывается на `/start` — то есть на действии, которое человек и так
    делает, когда что-то выглядит сломанным.
    """
    if bot is None:
        return
    try:
        await bot.set_chat_menu_button(
            chat_id=chat_id, menu_button=MenuButtonDefault()
        )
    except Exception:
        # Кнопка — удобство. Уронить из-за неё ответ на /start нельзя.
        logger.exception("menu button: не удалось снять персональную кнопку")


__all__ = ["BUTTON_TEXT", "drop_chat_override", "ensure_menu_button"]
