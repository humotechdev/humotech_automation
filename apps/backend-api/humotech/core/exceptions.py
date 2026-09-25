"""Ответы DRF на доменные ошибки.

Тело ответа то же, что было в контракте до перехода:
`{"error": {"code", "message", "details"}}`. Клиенты (React CRM, бот, Mini App)
различают ситуации по `code`, а не по тексту: текст переводится и переписывается,
код — нет.
"""

from __future__ import annotations

import logging

from django.core.exceptions import RequestDataTooBig
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DataError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback

from humotech.core.errors import DomainError

logger = logging.getLogger(__name__)


def _error(code: str, message: str, status: int, details=None) -> Response:
    return Response(
        {"error": {"code": code, "message": message, "details": details or None}},
        status=status,
    )


def _django_validation_messages(exc: DjangoValidationError) -> list[str]:
    """Тексты ошибки Django — короткие и без внутренностей.

    Это сообщения полей («“abc” не является верным UUID»), а не текст
    драйвера. Длина всё равно режется: в сообщение подставляется ввод
    клиента, и эхо стокилобайтной строки в ответе ни к чему.
    """
    return [str(message)[:200] for message in exc.messages[:5]]


def _input_error_response(exc) -> Response | None:
    """Исключения, которые на деле означают «клиент прислал мусор».

    Без этой ветки они доходят до Django как необработанные и превращаются
    в 500 с трейсбеком в журнале на каждый кривой запрос:

      * `ValidationError` Django — ORM сам проверяет значение фильтра:
        `.get(id="abc")` для UUID-колонки, `pk` из роутера DRF
        (`[^/.]+` пропускает что угодно), `office_id=abc` в query;
      * `DataError` — PostgreSQL отверг значение: NUL-байт в строке
        (psycopg 3 не отправляет его вовсе), строка длиннее колонки,
        число вне диапазона. Транзакция после неё испорчена, поэтому
        откат обязателен;
      * `RecursionError` — JSON глубиной в тысячу уровней: стандартный
        разбор упирается в предел рекурсии раньше, чем в любую проверку;
      * `OverflowError` — дата на краю календаря (`9999-12-31` плюс сутки).
        Страховка: такие места чинятся точечно, но пропущенное не должно
        давать 500;
      * `RequestDataTooBig` — тело больше `DATA_UPLOAD_MAX_MEMORY_SIZE`.

    `ValueError`/`TypeError` сюда намеренно не входят: слишком часто это
    ошибка в коде, а не во вводе, и ответ 400 спрятал бы её от журнала.
    """
    if isinstance(exc, DjangoValidationError):
        set_rollback()
        return _error(
            "validation_error",
            "Некорректное значение параметра",
            400,
            {"messages": _django_validation_messages(exc)},
        )
    if isinstance(exc, DataError):
        set_rollback()
        return _error(
            "validation_error",
            "Значение не может быть сохранено: недопустимые символы "
            "или слишком длинная строка",
            400,
        )
    if isinstance(exc, RecursionError):
        logger.warning("Отклонён запрос со слишком глубокой вложенностью")
        return _error("parse_error", "Слишком глубокая вложенность данных", 400)
    if isinstance(exc, OverflowError):
        logger.warning("Отклонён запрос с датой или числом вне диапазона")
        return _error("validation_error", "Дата или число вне допустимого диапазона", 400)
    if isinstance(exc, RequestDataTooBig):
        return _error("payload_too_large", "Слишком большое тело запроса", 413)
    return None


def domain_exception_handler(exc, context):
    """Доменная ошибка -> устойчивое тело ответа; остальное — как в DRF."""
    if isinstance(exc, DomainError):
        return Response(exc.as_dict(), status=exc.http_status)

    converted = _input_error_response(exc)
    if converted is not None:
        return converted

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
                "details": _details(detail, message),
            }
        }
    return response


def _details(detail, message):
    """Что положить в `details` рядом с текстом.

    Исключение может нести не только текст: отказ во входе, например,
    добавляет `reason`, по которому бот выбирает формулировку человеку.
    Без этой ветки всё, кроме `detail`, молча терялось бы, и клиент
    различал бы причины по тексту сообщения — то есть перестал бы их
    различать при первом же переводе.
    """
    if message is None:
        return detail
    extra = {key: value for key, value in detail.items() if key != "detail"}
    return extra or None
