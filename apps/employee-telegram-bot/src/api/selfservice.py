"""Обращения бота к личному кабинету сотрудника.

Токена сотрудника здесь нет ни в одном методе, и это решение, а не упущение.
Бот предъявляет два заголовка: общий секрет и подтверждённый Telegram ID.
Кто это, решает backend — заново на каждом запросе.

Что это даёт:

  * отзывать нечего. HR отключил привязку — доступ пропал со следующего
    действия, а не когда-нибудь по истечении срока;
  * хранить нечего. Ни в памяти процесса, ни в базе бота не лежит ничего,
    что можно было бы предъявить от чужого имени;
  * подделать нечего. `X-Telegram-User-Id` без верного секрета — просто
    число из запроса, и backend его не читает.

Плата — обращение к базе на каждое действие. Оно всё равно нужно, чтобы
узнать сотрудника.

Ни `employee_id`, ни `organization_id` бот не знает и не передаёт. Он их
и не может знать: в ответах их нет.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from src.api.errors import error_for
from src.config.settings import settings

BOT_SECRET_HEADER = "X-Bot-Token"
EMPLOYEE_HEADER = "X-Telegram-User-Id"


class SelfServiceClient:
    """Личный кабинет глазами бота."""

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self._base_url = (base_url or settings.backend_api_url).rstrip("/")
        self._timeout = aiohttp.ClientTimeout(
            total=timeout or settings.api_timeout_seconds
        )
        self._session: aiohttp.ClientSession | None = None

    # --- сотрудник -------------------------------------------------------

    async def profile(self, telegram_id: int) -> dict:
        return await self._get("/me/profile", telegram_id)

    async def status(self, telegram_id: int) -> dict:
        return await self._get("/me/status", telegram_id)

    async def statistics(self, telegram_id: int, period: str) -> dict:
        return await self._get(
            "/me/statistics", telegram_id, params={"period": period}
        )

    async def history(self, telegram_id: int, *, limit: int = 10) -> dict:
        return await self._get(
            "/me/history", telegram_id, params={"period": "month", "limit": limit}
        )

    async def absences(self, telegram_id: int, *, limit: int = 10) -> dict:
        return await self._get(
            "/me/absences", telegram_id, params={"limit": limit}
        )

    async def leave_balance(self, telegram_id: int) -> dict:
        return await self._get("/me/leave-balance", telegram_id)

    async def scan(
        self,
        *,
        telegram_user_id: int,
        token: str,
        client_event_id: str,
        latitude: float | None = None,
        longitude: float | None = None,
        accuracy_m: float | None = None,
    ) -> dict:
        """Отметка по QR за сотрудника.

        Отдельного адреса у этого пути нет: `/me/attendance/scan` давно
        принимает вход бота, и за ним стоит тот же сервис, что и за
        отметкой из открытого Mini App. Второй endpoint означал бы вторую
        систему посещаемости, которую пришлось бы чинить дважды.

        Что уходит: код, ключ попытки и координаты. Ни сотрудника, ни
        офиса, ни направления, ни времени — таких параметров у запроса
        нет, поэтому подделать их нечем. Сотрудника определяет сервер по
        Telegram ID, который подтвердил Telegram, а не отправитель.
        """
        body: dict = {"token": token, "client_event_id": client_event_id}
        if latitude is not None and longitude is not None:
            body["latitude"] = f"{latitude:.6f}"
            body["longitude"] = f"{longitude:.6f}"
            if accuracy_m is not None:
                body["accuracy_m"] = f"{accuracy_m:.2f}"

        return await self._request(
            "POST",
            "/me/attendance/scan",
            headers={EMPLOYEE_HEADER: str(telegram_user_id)},
            json=body,
        )

    # --- привязка ---------------------------------------------------------

    async def consume_link_token(
        self,
        *,
        token: str,
        telegram_user_id: int,
        telegram_chat_id: int,
        telegram_username: str | None = None,
        language_code: str | None = None,
    ) -> dict:
        """Сообщить backend токен из ссылки и подтверждённый Telegram.

        Ни сотрудника, ни организацию бот не передаёт и не знает: их
        определяет сам токен. Секрет здесь подтверждает, что Telegram ID
        пришёл от Telegram через нас, а не выдуман отправителем запроса.
        """
        return await self._request(
            "POST",
            "/telegram/bot/link",
            headers={},
            json={
                "token": token,
                "telegram_user_id": telegram_user_id,
                "telegram_chat_id": telegram_chat_id,
                "telegram_username": telegram_username,
                "language_code": language_code,
            },
        )

    # --- очередь уведомлений ---------------------------------------------

    async def claim_notifications(self) -> dict:
        """Забрать пачку сообщений. Только общий секрет, без сотрудника."""
        return await self._request("GET", "/telegram/bot/outbox", headers={})

    async def report_notifications(self, results: list[dict]) -> dict:
        return await self._request(
            "POST", "/telegram/bot/outbox", headers={}, json={"results": results}
        )

    # --- внутри ----------------------------------------------------------

    async def _get(
        self, path: str, telegram_id: int, *, params: dict | None = None
    ) -> Any:
        return await self._request(
            "GET", path, headers={EMPLOYEE_HEADER: str(telegram_id)}, params=params
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict,
        params: dict | None = None,
        json: dict | None = None,
    ) -> Any:
        session = await self._get_session()
        all_headers = {BOT_SECRET_HEADER: settings.backend_bot_secret, **headers}
        async with session.request(
            method,
            f"{self._base_url}{path}",
            headers=all_headers,
            params=params,
            json=json,
        ) as response:
            body = await self._body(response)
            if response.status >= 400:
                raise _error(response.status, body)
            return body

    async def _body(self, response) -> Any:
        try:
            return await response.json()
        except (aiohttp.ContentTypeError, ValueError):
            # Промежуточный прокси мог ответить HTML. Наружу это уходит как
            # обычная ошибка, а не как падение разбора.
            return {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


def _error(status: int, body: Any):
    """Ответ backend в общем виде -> ошибка бота.

    Формат ответа один на весь API: `{"error": {code, message, details}}`.
    Тело может оказаться и не таким — от промежуточного прокси, например, —
    и тогда берутся умолчания вместо падения на разборе.
    """
    payload = body if isinstance(body, dict) else {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    return error_for(
        status,
        error.get("code") or "error",
        error.get("message") or "Запрос не выполнен",
        error.get("details"),
    )


__all__ = ["BOT_SECRET_HEADER", "EMPLOYEE_HEADER", "SelfServiceClient"]
