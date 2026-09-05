"""Отметка, пришедшая служебным сообщением от Mini App.

Поток целиком: человек жмёт «📷 Отметиться» на нижней клавиатуре ->
Telegram открывает Mini App на `/scan?source=keyboard` -> экран берёт
геопозицию, показывает родной сканер и отдаёт попытку через
`sendData` -> Telegram закрывает приложение и присылает боту
`web_app_data` -> бот идёт в backend прежним путём -> результат
приходит сообщением сюда, в чат.

Почему так, а не запросом из самого приложения: кнопка нижней
клавиатуры не даёт Mini App подписи запуска. Доказать серверу, кто
пришёл, оттуда нечем, и единственный, кто может это сделать, — бот:
`message.from_user.id` проставляет сам Telegram, доставляя сообщение.

Второй системы посещаемости здесь нет. Бот зовёт тот же
`/me/attendance/scan`, который открыт ему давно (общий секрет плюс
подтверждённый Telegram ID), а за ним стоит тот же `scanning.scan()`
со всеми своими проверками: подпись кода, тридцать секунд, одноразовый
`jti`, блокировка сотрудника, серверное направление, серверное время,
журнал попыток.

Ни QR, ни координаты, ни подписи в сообщения человеку и в обычный
журнал не попадают: по строке кода собирают рабочий код, а по
координатам — маршрут человека.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.handlers.attendance.payload import Rejected, parse
from src.keyboards import employee as kb
from src.config.settings import settings
from src.messages import attendance as text

logger = logging.getLogger(__name__)

router = Router(name="attendance")


@router.message(F.web_app_data, F.chat.type == ChatType.PRIVATE)
async def scan_from_mini_app(message: Message, client: SelfServiceClient) -> None:
    """Одна попытка отметки. Личность — только от Telegram."""
    user = message.from_user
    if user is None:
        # Сообщение без отправителя (канал, анонимный админ) — не наш
        # случай: отмечать некого.
        return

    try:
        attempt = parse(message.web_app_data.data)
    except Rejected as bad:
        # В журнал — причина, не содержимое: там строка кода и координаты.
        logger.info("web_app_data rejected: %s", bad.reason)
        await answer(message, text.PAYLOAD_REJECTED)
        return

    try:
        result = await client.scan(
            telegram_user_id=user.id,
            token=attempt.qr,
            client_event_id=attempt.client_event_id,
            latitude=attempt.latitude,
            longitude=attempt.longitude,
            accuracy_m=attempt.accuracy_m,
        )
    except ApiError as error:
        reason = (error.details or {}).get("reason") if error.details else None
        logger.info("scan refused for %s: %s", user.id, reason or error.code)
        await answer(message, text.ACCESS_MESSAGES.get(reason, text.SCAN_FAILED))
        return
    except Exception:
        # Сеть или backend лежит. Человеку — понятная фраза, разбираться
        # по журналу.
        logger.exception("scan failed")
        await answer(message, text.SCAN_FAILED)
        return

    # След на удачном пути тоже нужен: по этой строке видно, что
    # служебное сообщение вообще дошло от Telegram до бота. Ни кода, ни
    # координат в ней нет — только исход и кто отметился.
    logger.info("scan handled for %s: %s", user.id, result.get("status"))
    await answer(message, text.outcome(result))


async def answer(message: Message, body: str) -> None:
    """Ответ с возвращённой клавиатурой.

    Клавиатуру прикладываем заново: Mini App закрылся, и человек снова
    в чате — кнопки должны быть под рукой, а не свёрнуты.
    """
    await message.answer(
        body,
        reply_markup=kb.employee_menu(
            settings.mini_app_url,
            private=getattr(message.chat, "type", "private") == "private",
        ),
    )


__all__ = ["router", "scan_from_mini_app"]
