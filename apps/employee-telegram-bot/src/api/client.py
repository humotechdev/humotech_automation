"""HTTP-клиент к backend-api.

Единственное место, через которое бот общается с внешним миром.
Прямых обращений к базе данных здесь нет и быть не может (правило №2 архитектуры).
Все методы повторяют packages/api-contracts/telegram-bot.v1.md один в один.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from src.api.errors import error_for
from src.config.settings import settings


class BackendClient:
    """Базовый интерфейс. Реализации: LiveBackendClient и StubBackendClient."""

    # --- привязка аккаунта (публичные) ---
    async def request_code(self, phone: str, telegram_id: int) -> dict: ...
    async def link(self, phone: str, code: str, telegram_id: int,
                   telegram_username: str | None) -> dict: ...
    async def unlink(self, token: str) -> None: ...

    # --- сотрудник ---
    async def me(self, token: str) -> dict: ...
    async def attendance_my(self, token: str, date_from: str, date_to: str) -> dict: ...
    async def attendance_summary(self, token: str, month: str) -> dict: ...
    async def create_vacation(self, token: str, date_from: str, date_to: str,
                              comment: str = "") -> dict: ...
    async def create_sick_leave(self, token: str, date_from: str, date_to: str,
                                comment: str = "",
                                attachment_file_id: str | None = None) -> dict: ...
    async def absences_my(self, token: str, status: str | None = None) -> dict: ...
    async def cancel_absence(self, token: str, absence_id: int) -> dict: ...
    async def create_correction(self, token: str, date: str, kind: str,
                                proposed_time: str, reason: str) -> dict: ...
    async def create_question(self, token: str, text: str,
                              category: str | None = None) -> dict: ...
    async def questions_my(self, token: str) -> dict: ...

    # --- HR ---
    async def hr_absences_pending(self, token: str, limit: int = 20,
                                  offset: int = 0) -> dict: ...
    async def hr_approve_absence(self, token: str, absence_id: int,
                                 comment: str = "") -> dict: ...
    async def hr_reject_absence(self, token: str, absence_id: int, reason: str) -> dict: ...
    async def hr_attendance_today(self, token: str, office_id: int | None = None) -> dict: ...
    async def hr_employees(self, token: str, query: str, limit: int = 20) -> dict: ...

    async def close(self) -> None: ...


class LiveBackendClient(BackendClient):
    """Реальные запросы к backend-api."""

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self._base_url = (base_url or settings.backend_api_url).rstrip("/")
        self._timeout = aiohttp.ClientTimeout(
            total=timeout or settings.api_timeout_seconds
        )
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _request(self, method: str, path: str, *, token: str | None = None,
                       json: dict | None = None,
                       params: dict | None = None) -> Any:
        session = await self._get_session()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"{self._base_url}{path}"

        async with session.request(method, url, json=json, params=params,
                                   headers=headers) as resp:
            if resp.status == 204:
                return None
            try:
                payload = await resp.json()
            except Exception:
                payload = {}

            if resp.status >= 400:
                err = (payload or {}).get("error") or {}
                raise error_for(
                    resp.status,
                    err.get("code", "internal_error"),
                    err.get("message", "Сервис временно недоступен"),
                    err.get("details"),
                )
            return payload

    # --- привязка ---
    async def request_code(self, phone, telegram_id):
        return await self._request("POST", "/auth/telegram/request-code",
                                   json={"phone": phone, "telegram_id": telegram_id})

    async def link(self, phone, code, telegram_id, telegram_username):
        return await self._request("POST", "/auth/telegram/link", json={
            "phone": phone, "code": code,
            "telegram_id": telegram_id, "telegram_username": telegram_username,
        })

    async def unlink(self, token):
        await self._request("POST", "/auth/telegram/unlink", token=token)

    # --- сотрудник ---
    async def me(self, token):
        return await self._request("GET", "/employees/me", token=token)

    async def attendance_my(self, token, date_from, date_to):
        return await self._request("GET", "/attendance/my", token=token,
                                   params={"from": date_from, "to": date_to})

    async def attendance_summary(self, token, month):
        return await self._request("GET", "/attendance/my/summary", token=token,
                                   params={"month": month})

    async def create_vacation(self, token, date_from, date_to, comment=""):
        return await self._request("POST", "/absences/vacation", token=token, json={
            "date_from": date_from, "date_to": date_to, "comment": comment,
        })

    async def create_sick_leave(self, token, date_from, date_to, comment="",
                                attachment_file_id=None):
        return await self._request("POST", "/absences/sick-leave", token=token, json={
            "date_from": date_from, "date_to": date_to, "comment": comment,
            "attachment_file_id": attachment_file_id,
        })

    async def absences_my(self, token, status=None):
        params = {"status": status} if status else None
        return await self._request("GET", "/absences/my", token=token, params=params)

    async def cancel_absence(self, token, absence_id):
        return await self._request("POST", f"/absences/{absence_id}/cancel", token=token)

    async def create_correction(self, token, date, kind, proposed_time, reason):
        return await self._request("POST", "/attendance/corrections", token=token, json={
            "date": date, "kind": kind,
            "proposed_time": proposed_time, "reason": reason,
        })

    async def create_question(self, token, text, category=None):
        return await self._request("POST", "/questions", token=token,
                                   json={"text": text, "category": category})

    async def questions_my(self, token):
        return await self._request("GET", "/questions/my", token=token)

    # --- HR ---
    async def hr_absences_pending(self, token, limit=20, offset=0):
        return await self._request("GET", "/hr/absences/pending", token=token,
                                   params={"limit": limit, "offset": offset})

    async def hr_approve_absence(self, token, absence_id, comment=""):
        return await self._request("POST", f"/hr/absences/{absence_id}/approve",
                                   token=token, json={"comment": comment})

    async def hr_reject_absence(self, token, absence_id, reason):
        return await self._request("POST", f"/hr/absences/{absence_id}/reject",
                                   token=token, json={"reason": reason})

    async def hr_attendance_today(self, token, office_id=None):
        params = {"office_id": office_id} if office_id else None
        return await self._request("GET", "/hr/attendance/today", token=token,
                                   params=params)

    async def hr_employees(self, token, query, limit=20):
        return await self._request("GET", "/hr/employees", token=token,
                                   params={"query": query, "limit": limit})

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
