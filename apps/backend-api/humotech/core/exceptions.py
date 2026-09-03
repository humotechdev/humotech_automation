"""Ответы DRF на доменные ошибки.

Тело ответа то же, что было в контракте до перехода:
`{"error": {"code", "message", "details"}}`. Клиенты (React CRM, бот, Mini App)
различают ситуации по `code`, а не по тексту: текст переводится и переписывается,
код — нет.
"""

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from humotech.core.errors import DomainError


def domain_exception_handler(exc, context):
    """Доменная ошибка -> устойчивое тело ответа; остальное — как в DRF."""
    if isinstance(exc, DomainError):
        return Response(exc.as_dict(), status=exc.http_status)

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    # Приводим ответы самого DRF к тому же виду, чтобы у клиента был
    # ровно один разбор ошибки, а не два.
    if not (isinstance(response.data, dict) and "error" in response.data):
        code = getattr(exc, "default_code", None) or "error"
        detail = response.data
        message = detail.get("detail") if isinstance(detail, dict) else None
        response.data = {
            "error": {
                "code": str(code),
                "message": str(message) if message else "Запрос не выполнен",
                "details": None if message else detail,
            }
        }
    return response
