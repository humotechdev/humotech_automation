"""Общая основа REST-слоя.

Слой намеренно тонкий: разобрать запрос, вызвать сервис, отдать результат.
Бизнес-правил здесь нет — они в сервисах, потому что те же правила понадобятся
Telegram-боту и командам обслуживания, а не только HTTP.

Проверки прав тоже остаются в сервисах: если бы их делал view, любой другой
вызывающий обошёл бы их молча.
"""

from __future__ import annotations

from rest_framework import serializers, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from humotech.core.openapi import ServiceViewSetAutoSchema
from humotech.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from humotech.core.rbac import Actor

UUID_PATTERN = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class CursorPageSerializer(serializers.Serializer):
    """Единая форма страницы для всех списков.

    `next_cursor` вместо номера страницы: постраничный вывод keyset-овый,
    и номера страниц в нём не существует — между запросами данные меняются.
    """

    items = serializers.ListField()
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class ServiceViewSet(viewsets.ViewSet):
    """Набор действий поверх сервиса.

    `ModelViewSet` здесь не подходит: он ходит в ORM сам, минуя сервис,
    а значит минуя проверки прав, область видимости и журнал.
    """

    permission_classes = [IsAuthenticated]
    # Все сущности проекта адресуются UUID. Роутер DRF по умолчанию
    # принимает в `pk` что угодно (`[^/.]+`), и `/employees/abc/` доходил
    # до ORM, где `.get(id="abc")` падал ValidationError — то есть 500.
    # Строгий шаблон отвечает 404 ещё на разборе адреса.
    lookup_value_regex = UUID_PATTERN
    # Генератор схемы не может вывести тип `id` без queryset-а, а его
    # здесь нет намеренно. См. `humotech/core/openapi.py`.
    schema = ServiceViewSetAutoSchema()
    # Класс сервиса и сериализатора задаёт наследник.
    service_class: type | None = None
    read_serializer_class: type[serializers.Serializer] | None = None

    def get_serializer_class(self):
        """Только ради генератора схемы OpenAPI.

        Сам набор действий этот метод не вызывает — сериализатор выбирается
        явно в `page_response` и `item_response`. Но без него генератор
        не может назвать тип ответа и молча выкидывает весь набор из
        схемы: у фронтенда получается документация без половины API.
        """
        return self.read_serializer_class

    def get_serializer(self, *args, **kwargs):
        serializer_class = self.get_serializer_class()
        if serializer_class is None:
            return None
        return serializer_class(*args, **kwargs)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        reject_nul_in_query(request)

    @property
    def actor(self) -> Actor:
        return Actor.from_user(self.request.user)

    @property
    def service(self):
        return self.service_class()

    # ------------------------------------------------------ разбор параметров

    def list_params(self) -> dict:
        """Общие параметры списков: поиск, статус, размер страницы, курсор."""
        query = self.request.query_params
        params: dict = {
            "search": query_text(query, "search"),
            "status": query_text(query, "status", max_length=64),
            "cursor": query_text(query, "cursor", max_length=1024),
        }
        limit = query.get("limit")
        if limit is not None:
            params["limit"] = self._positive_int(limit, "limit")
        return params

    @staticmethod
    def _positive_int(raw: str, field: str) -> int:
        return query_int_value(raw, field, minimum=1, maximum=MAX_PAGE_SIZE)

    # ------------------------------------------------------------- ответы

    def page_response(self, page: Page, serializer_class=None) -> Response:
        serializer = (serializer_class or self.read_serializer_class)(
            page.items, many=True
        )
        return Response(
            {
                "items": serializer.data,
                "next_cursor": page.next_cursor,
                "has_more": page.has_more,
            }
        )

    def item_response(self, instance, *, created: bool = False,
                      serializer_class=None) -> Response:
        serializer = (serializer_class or self.read_serializer_class)(instance)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


# --- разбор query-параметров ------------------------------------------------
#
# Общие помощники для всех view. Каждый отвечает ValidationFailed (400), а не
# исключением Python: `int("abc")`, `UUID("x")`, `date.fromisoformat("x")`
# без обёртки дают 500 и трейсбек в журнале на каждый кривой запрос.
# Повторяющийся параметр (`?limit=1&limit=99999`) читается как ПОСЛЕДНЕЕ
# значение — так же, как `QueryDict.get`, — и проходит те же проверки.

MAX_SEARCH_LENGTH = 200


def _fail(field: str, message: str, raw=None):
    from humotech.core.errors import ValidationFailed

    details = {"field": field}
    if raw is not None:
        details["value"] = str(raw)[:100]
    return ValidationFailed(message, details=details)


def query_text(query, name: str, *, max_length: int = MAX_SEARCH_LENGTH) -> str | None:
    """Строка из query: пустая -> None, NUL и сверхдлинная -> 400.

    NUL PostgreSQL в строке не принимает вовсе (psycopg отказывается её
    отправлять), а поиск по стокилобайтной строке — это ILIKE, который
    база честно прогонит по каждой строке таблицы.
    """
    raw = query.get(name)
    if not raw:
        return None
    if "\x00" in raw:
        raise _fail(name, f"Параметр «{name}» содержит недопустимый символ")
    if len(raw) > max_length:
        raise _fail(name, f"Параметр «{name}» длиннее {max_length} символов")
    return raw


def reject_nul_in_query(request) -> None:
    """NUL-байт в любом query-параметре — 400 до всякой обработки.

    PostgreSQL не хранит NUL в тексте, psycopg отказывается такую строку
    отправлять, а до базы она доходит через любой фильтр. Отказ здесь
    дешевле и понятнее, чем `DataError` посреди сервиса. Вызывается из
    `ServiceViewSet.initial`; остальные view страхует перевод `DataError`
    в 400 в `humotech.core.exceptions`.
    """
    for name, values in request.query_params.lists():
        if "\x00" in name or any("\x00" in value for value in values):
            raise _fail(name.replace("\x00", "")[:50],
                        "Параметр запроса содержит недопустимый символ")


def query_int_value(raw, name: str, *, minimum: int | None = None,
                    maximum: int | None = None) -> int:
    try:
        text = str(raw).strip()
        if len(text) > 20:  # int() на мегабайте цифр — тоже работа
            raise ValueError
        value = int(text)
    except (TypeError, ValueError) as exc:
        raise _fail(name, f"Параметр «{name}» должен быть целым числом", raw) from exc
    if (minimum is not None and value < minimum) or (
        maximum is not None and value > maximum
    ):
        bounds = f"от {minimum if minimum is not None else '-∞'} до " \
                 f"{maximum if maximum is not None else '∞'}"
        raise _fail(name, f"Параметр «{name}» должен быть {bounds}", raw)
    return value


def query_int(query, name: str, *, default: int | None = None,
              minimum: int | None = None, maximum: int | None = None) -> int | None:
    raw = query.get(name)
    if raw in (None, ""):
        return default
    return query_int_value(raw, name, minimum=minimum, maximum=maximum)


def query_uuid(query, name: str):
    import uuid as _uuid

    raw = query.get(name)
    if not raw:
        return None
    try:
        return _uuid.UUID(str(raw))
    except (TypeError, ValueError, AttributeError) as exc:
        raise _fail(name, f"Параметр «{name}» должен быть UUID", raw) from exc


def query_date(query, name: str):
    """Дата ISO `YYYY-MM-DD`. Годы вне 1900–2200 — тоже 400.

    Календарь Python формально знает даты с 1-го по 9999-й год, но на их
    краях ломается арифметика («следующие сутки» у 9999-12-31, сдвиг
    пояса у 0001-01-01), а кадрового смысла в них нет.
    """
    from datetime import date as _date

    raw = query.get(name)
    if not raw:
        return None
    try:
        value = _date.fromisoformat(str(raw))
    except (TypeError, ValueError) as exc:
        raise _fail(name, f"Параметр «{name}» должен быть датой ГГГГ-ММ-ДД", raw) from exc
    if not 1900 <= value.year <= 2200:
        raise _fail(name, f"Параметр «{name}» вне допустимого диапазона дат", raw)
    return value


def validated(serializer_class, data) -> dict:
    """Разбор входных данных с приведением ошибок DRF к общему виду."""
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


__all__ = [
    "CursorPageSerializer",
    "ServiceViewSet",
    "validated",
    "query_date",
    "query_int",
    "query_int_value",
    "query_text",
    "query_uuid",
    "DEFAULT_PAGE_SIZE",
]
