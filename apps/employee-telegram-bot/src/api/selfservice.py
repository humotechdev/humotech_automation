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

    async def ask_hr(
        self, telegram_id: int, *, text: str, message_id: int | None = None
    ) -> dict:
        """Сообщение в отдел кадров.

        В какое обращение оно ляжет — в открытое, переоткрытое или новое, —
        решает backend. `message_id` защищает от дубля, если бот отправит
        то же сообщение повторно после сбоя сети.
        """
        return await self._request(
            "POST",
            "/me/questions/messages",
            headers={EMPLOYEE_HEADER: str(telegram_id)},
            json={"text": text, "telegram_message_id": message_id},
        )

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

    async def day_notice(
        self,
        *,
        telegram_user_id: int,
        kind: str,
        comment: str | None = None,
    ) -> dict:
        """Ответ на напоминание: «Опаздываю» или «Не приду».

        Ни отпуска, ни больничного это не оформляет — они проходят
        согласование и живут своими заявками. Здесь только объяснение
        пустой строки в табеле.
        """
        body: dict = {"kind": kind}
        if comment:
            body["comment"] = comment
        return await self._request(
            "POST",
            "/me/attendance/notice",
            headers={EMPLOYEE_HEADER: str(telegram_user_id)},
            json=body,
        )

    async def upload_absence_document(
        self,
        *,
        telegram_user_id: int,
        request_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> dict:
        """Приложить справку к заявке файлом из чата.

        Тот же адрес, что у Mini App: второй путь загрузки означал бы
        вторую проверку типа и размера, которую однажды забудут
        обновить. Проверяет файл сервер — бот только передаёт его.
        """
        form = aiohttp.FormData()
        form.add_field(
            "document", content, filename=filename, content_type=content_type
        )
        session = await self._get_session()
        headers = {
            BOT_SECRET_HEADER: settings.backend_bot_secret,
            EMPLOYEE_HEADER: str(telegram_user_id),
        }
        async with session.post(
            f"{self._base_url}/me/absences/{request_id}/document",
            headers=headers,
            data=form,
        ) as response:
            body = await self._body(response)
            if response.status >= 400:
                raise _error(response.status, body)
            return body

    async def ask(
        self, telegram_id: int, *, text: str, client_request_id: str | None = None
    ) -> dict:
        """Спросить ассистента.

        Обращение в CRM здесь НЕ создаётся: вопрос, на который ассистент
        ответил, кадровику не нужен, а очередь, забитая тем, что
        решилось само, перестаёт быть очередью.
        """
        body: dict = {"text": text}
        if client_request_id:
            body["client_request_id"] = client_request_id
        return await self._request(
            "POST", "/me/ask",
            headers={EMPLOYEE_HEADER: str(telegram_id)},
            json=body,
        )

    async def escalate(
        self, telegram_id: int, *, text: str, message_id: int | None = None
    ) -> dict:
        """Передать вопрос HR — только по явному нажатию человека.

        Уходит ИСХОДНЫЙ вопрос, а не ответ ассистента: кадровик должен
        прочитать то, что написал сотрудник.
        """
        body: dict = {"text": text}
        if message_id is not None:
            body["telegram_message_id"] = message_id
        return await self._request(
            "POST", "/me/ask/escalate",
            headers={EMPLOYEE_HEADER: str(telegram_id)},
            json=body,
        )

    # --- первичное ознакомление -------------------------------------------

    async def onboarding(self, telegram_id: int) -> dict:
        """Где человек остановился и что показать дальше.

        Следующий шаг выбирает сервер. Бот не просит «покажи седьмую
        карточку»: кнопка в Telegram живёт в чате вечно, её можно нажать
        через месяц из старого сообщения, и порядок, держащийся на ней,
        порядком быть перестаёт.
        """
        return await self._get("/me/onboarding", telegram_id)

    async def onboarding_start(
        self, telegram_id: int, *, message_id: int | None = None
    ) -> dict:
        return await self._request(
            "POST", "/me/onboarding/start",
            headers={EMPLOYEE_HEADER: str(telegram_id)},
            json={"message_id": message_id},
        )

    async def onboarding_section(self, telegram_id: int, position: int) -> dict:
        """Карточка по номеру — для «← Назад» и перечитывания из меню."""
        return await self._get(
            f"/me/onboarding/sections/{position}", telegram_id
        )

    async def onboarding_acknowledge(
        self, telegram_id: int, section_id: str, *, message_id: int | None = None
    ) -> dict:
        """«Я ознакомился». Повтор безопасен: ключ в базе решает это молча."""
        return await self._request(
            "POST", "/me/onboarding/acknowledge",
            headers={EMPLOYEE_HEADER: str(telegram_id)},
            json={"section_id": section_id, "message_id": message_id},
        )

    async def onboarding_decision(
        self, telegram_id: int, version_id: str, decision: str
    ) -> dict:
        return await self._request(
            "POST", "/me/onboarding/decision",
            headers={EMPLOYEE_HEADER: str(telegram_id)},
            json={"version_id": version_id, "decision": decision},
        )

    async def policy_text(self, telegram_id: int, version_id: str) -> dict:
        """Полный текст редакции — то, что открывает отдельная кнопка."""
        return await self._get(f"/me/policies/{version_id}", telegram_id)

    async def policy_file(
        self, telegram_id: int, version_id: str
    ) -> tuple[bytes, str, str]:
        """Утверждённый PDF: содержимое, имя файла и тип.

        Скачивается здесь, а не отдаётся ссылкой: файл лежит в приватном
        хранилище, и адреса, который можно переслать, у него нет и быть
        не должно.
        """
        session = await self._get_session()
        headers = {
            BOT_SECRET_HEADER: settings.backend_bot_secret,
            EMPLOYEE_HEADER: str(telegram_id),
        }
        async with session.get(
            f"{self._base_url}/me/policies/{version_id}/file", headers=headers
        ) as response:
            if response.status >= 400:
                raise _error(response.status, await self._body(response))
            name = "document.pdf"
            disposition = response.headers.get("Content-Disposition", "")
            if "filename=" in disposition:
                name = disposition.split("filename=")[-1].strip('"; ')
            return (
                await response.read(),
                name,
                response.headers.get("Content-Type", "application/pdf"),
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

    async def accept_link_terms(self, *, telegram_user_id: int) -> dict:
        return await self._request(
            "POST", "/telegram/bot/link/accept", headers={},
            json={"telegram_user_id": telegram_user_id},
        )

    async def recognize(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        telegram_username: str | None,
        language_code: str | None = None,
    ) -> dict:
        """Спросить backend: не ждут ли этого человека.

        Бот не может написать первым — это правило Telegram. Всё, что он
        может, — узнать открывшего его человека по имени в Telegram,
        которое кадровик указал в карточке.

        Ответ — то, чем поздороваться: имя, офис, график, руководитель.
        Доступа это не даёт: привязка ждёт подтверждения кадровика.
        """
        return await self._request(
            "POST",
            "/telegram/bot/recognize",
            headers={},
            json={
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
