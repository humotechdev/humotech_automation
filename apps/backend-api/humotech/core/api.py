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

from humotech.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from humotech.core.rbac import Actor


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
    # Класс сервиса и сериализатора задаёт наследник.
    service_class: type | None = None
    read_serializer_class: type[serializers.Serializer] | None = None

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
            "search": query.get("search") or None,
            "status": query.get("status") or None,
            "cursor": query.get("cursor") or None,
        }
        limit = query.get("limit")
        if limit is not None:
            params["limit"] = self._positive_int(limit, "limit")
        return params

    @staticmethod
    def _positive_int(raw: str, field: str) -> int:
        from humotech.core.errors import ValidationFailed

        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed(
                f"Параметр «{field}» должен быть числом",
                details={"field": field, "value": raw},
            ) from exc
        if value < 1 or value > MAX_PAGE_SIZE:
            raise ValidationFailed(
                f"Параметр «{field}» должен быть от 1 до {MAX_PAGE_SIZE}",
                details={"field": field, "value": value},
            )
        return value

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


def validated(serializer_class, data) -> dict:
    """Разбор входных данных с приведением ошибок DRF к общему виду."""
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


__all__ = [
    "CursorPageSerializer",
    "ServiceViewSet",
    "validated",
    "DEFAULT_PAGE_SIZE",
]
