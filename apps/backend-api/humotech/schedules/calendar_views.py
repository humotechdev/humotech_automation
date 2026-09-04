"""REST-интерфейс календарных исключений.

Отдельный модуль, а не дополнение к `schedules/views.py`: там графики
работы, здесь — праздники и переносы. Общего у них только приложение.
"""

from __future__ import annotations

import uuid
from datetime import date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import CALENDAR_EXCEPTION_TYPES
from humotech.core.errors import ValidationFailed
from humotech.schedules.calendar import MAX_RANGE_DAYS, CalendarExceptionService


class CalendarExceptionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    date = serializers.DateField()
    name = serializers.CharField()
    exception_type = serializers.ChoiceField(choices=CALENDAR_EXCEPTION_TYPES)
    is_working_day = serializers.BooleanField(
        help_text="Выводится из типа, отдельно не задаётся",
    )
    reason = serializers.CharField(allow_null=True)
    is_active = serializers.BooleanField(
        help_text="Снятое исключение остаётся в истории, но на расчёт "
                  "не влияет",
    )
    office_id = serializers.UUIDField(
        allow_null=True, help_text="null — исключение на всю организацию",
    )
    office_name = serializers.CharField(
        source="office.name", allow_null=True, default=None,
    )
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class CalendarExceptionCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    exception_type = serializers.ChoiceField(choices=CALENDAR_EXCEPTION_TYPES)
    date_from = serializers.DateField()
    date_to = serializers.DateField(
        required=False,
        allow_null=True,
        help_text=(
            "Последний день периода включительно. Период разворачивается "
            f"в отдельную строку на каждый день, не больше {MAX_RANGE_DAYS}."
        ),
    )
    office_id = serializers.UUIDField(required=False, allow_null=True)
    region_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text=(
            "Разворачивается в строки по действующим офисам региона. "
            "Собственной строки у региона нет."
        ),
    )
    reason = serializers.CharField(
        required=False, allow_blank=True, allow_null=True,
    )


class CalendarExceptionUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    exception_type = serializers.ChoiceField(
        choices=CALENDAR_EXCEPTION_TYPES, required=False,
    )
    reason = serializers.CharField(
        required=False, allow_blank=True, allow_null=True,
    )


class CalendarExceptionListSerializer(serializers.Serializer):
    """Ответ на создание: период — это несколько строк, а не одна."""

    items = CalendarExceptionSerializer(many=True)


def _uuid(request, name: str) -> uuid.UUID | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError) as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть UUID", details={"field": name}
        ) from exc


def _date(request, name: str) -> date | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError) as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть датой в формате ГГГГ-ММ-ДД",
            details={"field": name, "value": raw},
        ) from exc


@extend_schema(tags=["Календарь"])
class CalendarExceptionViewSet(ServiceViewSet):
    """Праздники, сокращённые дни и рабочие выходные.

    Изменение календаря сразу меняет расчёт: присутствие, дашборд и
    аналитика читают исключения на каждом запросе, ничего не кешируя.
    """

    service_class = CalendarExceptionService
    read_serializer_class = CalendarExceptionSerializer

    @extend_schema(
        summary="Список исключений календаря",
        parameters=[
            OpenApiParameter(
                "office_id", str,
                description="Исключения офиса плюс общие по организации",
            ),
            OpenApiParameter("region_id", str),
            OpenApiParameter(
                "scope", str, enum=["organization"],
                description="organization — только общие по организации",
            ),
            OpenApiParameter("date_from", OpenApiTypes.DATE),
            OpenApiParameter("date_to", OpenApiTypes.DATE),
            OpenApiParameter(
                "exception_type", str, enum=list(CALENDAR_EXCEPTION_TYPES),
            ),
            OpenApiParameter("search", str, description="Подстрока в названии"),
            OpenApiParameter(
                "include_inactive", OpenApiTypes.BOOL,
                description="true — показать и снятые исключения",
            ),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
    )
    def list(self, request):
        params = self.list_params()
        params.pop("status", None)  # у исключения нет статуса
        return self.page_response(
            self.service.list(
                self.actor,
                **params,
                office_id=_uuid(request, "office_id"),
                region_id=_uuid(request, "region_id"),
                scope=request.query_params.get("scope") or None,
                date_from=_date(request, "date_from"),
                date_to=_date(request, "date_to"),
                exception_type=request.query_params.get("exception_type") or None,
                include_inactive=(
                    request.query_params.get("include_inactive") == "true"
                ),
            )
        )

    @extend_schema(summary="Одно исключение календаря")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    @extend_schema(
        summary="Завести исключение на день или период",
        request=CalendarExceptionCreateSerializer,
        responses={201: CalendarExceptionListSerializer},
        examples=[
            OpenApiExample(
                "Праздник на всю организацию",
                value={"name": "Навруз", "exception_type": "HOLIDAY",
                       "date_from": "2026-03-21", "date_to": "2026-03-24"},
                request_only=True,
            ),
            OpenApiExample(
                "Рабочая суббота в одном офисе",
                value={"name": "Перенос за 23 марта",
                       "exception_type": "WORKING_WEEKEND",
                       "date_from": "2026-03-28",
                       "office_id": "0f2a…", "reason": "приказ №14 от 03.03"},
                request_only=True,
            ),
        ],
    )
    def create(self, request):
        payload = validated(CalendarExceptionCreateSerializer, request.data)
        created = self.service.create(self.actor, **payload)
        return Response(
            {"items": CalendarExceptionSerializer(created, many=True).data},
            status=201,
        )

    @extend_schema(
        summary="Изменить название, тип или основание",
        description=(
            "Дата и офис правкой поля не меняются: это не исправление "
            "опечатки, а перенос исключения. Снимите одно и заведите другое."
        ),
        request=CalendarExceptionUpdateSerializer,
        responses={200: CalendarExceptionSerializer},
    )
    def partial_update(self, request, pk=None):
        payload = validated(CalendarExceptionUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))

    @extend_schema(
        summary="Снять исключение с действия",
        description=(
            "Строка остаётся, но на расчёт не влияет: присутствие, "
            "аналитика, статистика и отсутствия читают только "
            "действующие исключения. "
            "Так снимают отменённый приказом перенос. Сам факт «в марте "
            "собирались работать в субботу, потом отменили» через "
            "полгода объясняет расхождение в табеле."
        ),
        request=None,
        responses={200: CalendarExceptionSerializer},
    )
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(self.service.deactivate(self.actor, pk))

    @extend_schema(
        summary="Вернуть снятое исключение в действие",
        description=(
            "Отказ, если на эту дату уже действует другое исключение: "
            "одна дата — одно действующее утверждение о дне, и держит "
            "это правило база."
        ),
        request=None,
        responses={200: CalendarExceptionSerializer},
    )
    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self.item_response(self.service.reactivate(self.actor, pk))

    @extend_schema(
        summary="Удалить исключение целиком",
        description=(
            "Для опечаток: заведено не на тот день или не в тот офис, "
            "и такой строки не должно было быть вовсе. Отменённый "
            "приказом перенос снимают действием deactivate — он остаётся "
            "в календаре прошлого. "
            "Снимок удалённого остаётся в журнале изменений."
        ),
        responses={204: None},
    )
    def destroy(self, request, pk=None):
        self.service.delete(self.actor, pk)
        return Response(status=204)


__all__ = ["CalendarExceptionViewSet"]
