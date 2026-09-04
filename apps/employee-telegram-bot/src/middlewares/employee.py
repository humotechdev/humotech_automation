"""Кто пишет боту: спрашиваем backend на каждом обновлении.

Токенов нет, кэша прав нет. Оба они означали бы одно и то же: какое-то
время после отзыва привязки человек ещё что-то может. Здесь такого времени
нет вовсе — доступ проверяется заново, и «отключил» значит «сразу».

В `data` кладётся:

    employee  — профиль или None;
    denial    — причина отказа или None;
    client    — клиент личного кабинета.

Причина отказа нужна не для журнала, а для формулировки: человеку в разных
состояниях надо делать разное. «Ждём подтверждения отдела кадров» и
«попросите ссылку» — разные действия, и одинаковый ответ на них отправил бы
половину людей не туда.

Профиль здесь НЕ кэшируется. Один лишний запрос на нажатие кнопки дешевле,
чем минута, в течение которой уволенный ещё видит свои данные.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from src.api.errors import ApiError, Unauthorized
from src.api.selfservice import SelfServiceClient

logger = logging.getLogger(__name__)

# Что backend называет причиной отказа. Расшифровываются они в текстах;
# здесь важно только то, что причина доезжает до хендлера целиком.
REASON_UNAVAILABLE = "backend_unavailable"


class EmployeeMiddleware(BaseMiddleware):
    def __init__(self, client: SelfServiceClient) -> None:
        self.client = client

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        data["client"] = self.client
        data["employee"] = None
        data["denial"] = None

        if user is not None:
            try:
                data["employee"] = await self.client.profile(user.id)
            except Unauthorized as error:
                data["denial"] = _reason(error)
            except ApiError as error:
                # Сеть или сервер. Это НЕ отказ в доступе: сказать человеку
                # «нет доступа» из-за упавшего backend значит отправить его
                # в отдел кадров разбираться с тем, чего не происходило.
                logger.warning("backend unavailable: %s", error)
                data["denial"] = REASON_UNAVAILABLE

        return await handler(event, data)


def _reason(error: Unauthorized) -> str:
    """Причина отказа из тела ответа.

    Backend отдаёт её боту точно — бот предъявил общий секрет, то есть он
    своя сторона. Наружу, в Mini App, та же причина уходит усечённой.
    """
    details = error.details if isinstance(error.details, dict) else {}
    return details.get("reason") or "not_linked"


__all__ = ["EmployeeMiddleware", "REASON_UNAVAILABLE"]
