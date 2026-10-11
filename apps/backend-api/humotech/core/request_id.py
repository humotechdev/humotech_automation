"""Идентификатор запроса: сквозная метка для логов (аудит 12.10, A-42).

Без неё строки логов одного запроса не связать между собой, а жалобу
«в 10:15 не сохранилось» не сопоставить с ошибкой на сервере. Метка:
  * берётся из заголовка `X-Request-ID`, если его поставил шлюз и он
    безопасен (буквы, цифры, `-_.:`, до 64 символов) — иначе создаётся новая;
  * попадает в каждую строку лога (`%(request_id)s`) через фильтр;
  * возвращается клиенту в заголовке `X-Request-ID` — её можно назвать
    в обращении в поддержку.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar

from django.utils.deprecation import MiddlewareMixin

HEADER = "X-Request-ID"
_SAFE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_current: ContextVar[str] = ContextVar("humotech_request_id", default="-")


def current_request_id() -> str:
    return _current.get()


class RequestIdMiddleware(MiddlewareMixin):
    def process_request(self, request):
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        value = incoming if _SAFE.match(incoming or "") else uuid.uuid4().hex
        request.request_id = value
        request._request_id_token = _current.set(value)

    def process_response(self, request, response):
        value = getattr(request, "request_id", None)
        if value:
            response[HEADER] = value
        token = getattr(request, "_request_id_token", None)
        if token is not None:
            try:
                _current.reset(token)
            except ValueError:
                # Другой контекст (стриминг под ASGI) — метка истечёт сама.
                pass
        return response


class RequestIdLogFilter(logging.Filter):
    """Добавляет `request_id` в каждую запись лога (вне запроса — `-`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            # `django.request` пишет «Forbidden/Not Found» уже после ответа,
            # когда метка сброшена, — но передаёт сам запрос в `extra`.
            request = getattr(record, "request", None)
            record.request_id = (getattr(request, "request_id", None)
                                 or current_request_id())
        return True
