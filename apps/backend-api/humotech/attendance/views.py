"""REST-интерфейс посещаемости для CRM.

Слой тонкий, как и весь REST в проекте: разобрать параметры, вызвать
сервис, отдать результат. Права и область видимости проверяет сервис —
если бы это делал view, любой другой вызывающий обошёл бы проверку молча.

Параметры фильтрации перечислены поимённо и нигде не раскрываются из
`request.query_params` целиком. Это не занудство: `organization_id`,
пришедший от клиента, не должен иметь ни одного способа попасть в запрос
к базе, и перечисление руками — единственная защита, которая не ломается
при добавлении поля.
"""

from __future__ import annotations

import uuid
from datetime import date

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers as drf_serializers
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.attendance.hr import PRESENCE_STATES, AttendanceHrService
from humotech.attendance.serializers import (
    AttendanceEventSerializer,
    AttendanceSessionSerializer,
    CorrectionDecisionSerializer,
    CorrectionRequestSerializer,
    ManualEventSerializer,
    PresenceRowSerializer,
)
from humotech.core.api import ServiceViewSet, validated
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor


# Параметры, общие для журналов. Перечислены здесь один раз: схема
# и код читают один и тот же список, и разъехаться им негде.
SCOPE_PARAMS = [
    OpenApiParameter("employee_id", str, description="Один сотрудник"),
    OpenApiParameter("office_id", str, description="Один офис"),
    OpenApiParameter("region_id", str, description="Все офисы региона"),
    OpenApiParameter("date_from", str, description="Начало периода, ГГГГ-ММ-ДД"),
    OpenApiParameter("date_to", str, description="Конец периода включительно"),
    OpenApiParameter("cursor", str, description="Курсор следующей страницы"),
    OpenApiParameter("limit", int, description="Размер страницы, до 200"),
]


class PresenceResponseSerializer(drf_serializers.Serializer):
    """Ответ экрана присутствия. Нужен схеме; в коде не используется."""

    date = drf_serializers.DateField()
    timezone = drf_serializers.CharField()
    counts = drf_serializers.DictField(child=drf_serializers.IntegerField())
    total = drf_serializers.IntegerField()
    items = PresenceRowSerializer(many=True)


class PageSerializer(drf_serializers.Serializer):
    next_cursor = drf_serializers.CharField(allow_null=True)
    has_more = drf_serializers.BooleanField()


class EventPageSerializer(PageSerializer):
    items = AttendanceEventSerializer(many=True)


class SessionPageSerializer(PageSerializer):
    items = AttendanceSessionSerializer(many=True)


class CorrectionPageSerializer(PageSerializer):
    items = CorrectionRequestSerializer(many=True)


def _uuid_param(request, name: str) -> uuid.UUID | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть UUID", details={"field": name, "value": raw}
        ) from exc


def _date_param(request, name: str) -> date | None:
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


def _flag(request, name: str) -> bool:
    return (request.query_params.get(name) or "").lower() in ("1", "true", "yes")


class AttendanceViewSet(ServiceViewSet):
    """Списки событий и сессий."""

    service_class = AttendanceHrService

    def _common(self, request) -> dict:
        return {
            "employee_id": _uuid_param(request, "employee_id"),
            "office_id": _uuid_param(request, "office_id"),
            "region_id": _uuid_param(request, "region_id"),
            "date_from": _date_param(request, "date_from"),
            "date_to": _date_param(request, "date_to"),
        }

    @extend_schema(
        summary="Журнал сканирований",
        description=(
            "Сырые события отметки, включая отклонённые. Только чтение: "
            "строка события не меняется никогда, а исправление — это "
            "решение по заявке или новое событие с source = MANUAL."
        ),
        parameters=SCOPE_PARAMS
        + [
            OpenApiParameter("event_type", str, enum=["ENTRY", "EXIT"]),
            OpenApiParameter("source", str, enum=["QR", "MANUAL", "IMPORT"]),
            OpenApiParameter(
                "verification_status", str,
                enum=["ACCEPTED", "REJECTED", "REVIEW"],
            ),
        ],
        responses=EventPageSerializer,
        tags=["Посещаемость"],
    )
    def events(self, request):
        params = self._common(request)
        page = self.service.events(
            self.actor,
            **params,
            event_type=request.query_params.get("event_type") or None,
            source=request.query_params.get("source") or None,
            verification_status=(
                request.query_params.get("verification_status") or None
            ),
            **self._paging(request),
        )
        return self.page_response(page, serializer_class=AttendanceEventSerializer)

    @extend_schema(
        summary="Рабочие сессии",
        parameters=SCOPE_PARAMS
        + [
            OpenApiParameter(
                "status", str,
                enum=["OPEN", "CLOSED", "CORRECTED", "INVALID"],
            ),
            OpenApiParameter(
                "open", bool,
                description="Только незакрытые сессии, без времени выхода",
            ),
        ],
        responses=SessionPageSerializer,
        tags=["Посещаемость"],
    )
    def sessions(self, request):
        params = self._common(request)
        page = self.service.sessions(
            self.actor,
            **params,
            status=request.query_params.get("status") or None,
            only_open=_flag(request, "open"),
            **self._paging(request),
        )
        return self.page_response(page, serializer_class=AttendanceSessionSerializer)

    def _paging(self, request) -> dict:
        paging: dict = {"cursor": request.query_params.get("cursor") or None}
        limit = request.query_params.get("limit")
        if limit is not None:
            paging["limit"] = self._positive_int(limit, "limit")
        return paging


class PresenceView(APIView):
    """Кто где на выбранный день.

    Ответ отдаётся целиком, без страниц: по нему считаются карточки
    дашборда, а итог по первым пятидесяти строкам — это не итог. Размер
    ограничивает область видимости, а не параметр от клиента.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Кто где на выбранный день",
        description=(
            "Состав смены целиком, без страниц: по этому ответу считаются "
            "карточки дашборда, а итог по первым пятидесяти строкам — "
            "не итог. Поле counts содержит те же числа, что и карточки."
        ),
        parameters=[
            OpenApiParameter("date", str, description="День, ГГГГ-ММ-ДД"),
            OpenApiParameter("office_id", str),
            OpenApiParameter("region_id", str),
            OpenApiParameter("department_id", str),
            OpenApiParameter("position_id", str),
            OpenApiParameter("schedule_id", str),
            OpenApiParameter(
                "state", str, enum=list(PRESENCE_STATES),
                description="Оставить только одно состояние",
            ),
            OpenApiParameter("search", str, description="Поиск по ФИО и номеру"),
        ],
        responses=PresenceResponseSerializer,
        tags=["Посещаемость"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        report = AttendanceHrService().presence(
            actor,
            day=_date_param(request, "date"),
            office_id=_uuid_param(request, "office_id"),
            region_id=_uuid_param(request, "region_id"),
            department_id=_uuid_param(request, "department_id"),
            position_id=_uuid_param(request, "position_id"),
            schedule_id=_uuid_param(request, "schedule_id"),
            state=request.query_params.get("state") or None,
            search=request.query_params.get("search") or None,
        )
        return Response(
            {
                "date": report.day.isoformat(),
                "timezone": report.timezone,
                # Сводка рядом со строками: дашборд берёт числа отсюда,
                # а не пересчитывает их у себя. Один источник — одна правда.
                "counts": report.counts(),
                "total": len(report.rows),
                "items": PresenceRowSerializer(report.rows, many=True).data,
            }
        )


class CorrectionListView(APIView):
    """Заявки на исправление отметок."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Заявки на исправление отметок",
        parameters=[
            OpenApiParameter(
                "status", str,
                enum=["DRAFT", "SUBMITTED", "IN_REVIEW", "APPROVED",
                      "REJECTED", "CANCELLED"],
            ),
            OpenApiParameter("employee_id", str),
            OpenApiParameter("office_id", str),
            OpenApiParameter("region_id", str),
            OpenApiParameter("cursor", str),
        ],
        responses=CorrectionPageSerializer,
        tags=["Посещаемость"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        page = AttendanceHrService().corrections(
            actor,
            status=request.query_params.get("status") or None,
            employee_id=_uuid_param(request, "employee_id"),
            office_id=_uuid_param(request, "office_id"),
            region_id=_uuid_param(request, "region_id"),
            cursor=request.query_params.get("cursor") or None,
        )
        return Response(
            {
                "items": CorrectionRequestSerializer(page.items, many=True).data,
                "next_cursor": page.next_cursor,
                "has_more": page.has_more,
            }
        )


class CorrectionDecisionView(APIView):
    """Одобрить или отклонить заявку.

    Решение в пути, а не в теле: две разные операции с разными правами
    на разных адресах читаются в журнале доступа без разбора тела запроса.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Решение по заявке на исправление",
        description=(
            "decision — approve или reject. Событие при этом не "
            "переписывается: меняется расчётная сессия, а решение остаётся "
            "в журнале с автором, временем и причиной."
        ),
        request=CorrectionDecisionSerializer,
        responses=CorrectionRequestSerializer,
        tags=["Посещаемость"],
    )
    def post(self, request, request_id, decision):
        actor = Actor.from_user(request.user)
        payload = validated(CorrectionDecisionSerializer, request.data)
        result = AttendanceHrService().review_correction(
            actor,
            request_id,
            decision=decision,
            comment=payload.get("comment"),
        )
        return Response(CorrectionRequestSerializer(result).data)


class ManualEventView(APIView):
    """Ручная отметка кадровика.

    Это добавление события, а не правка существующего: `source = MANUAL`
    отличает её навсегда, причина обязательна.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Ручная отметка кадровика",
        description=(
            "Добавляет НОВОЕ событие с source = MANUAL. Причина "
            "обязательна: по этим отметкам считают рабочее время."
        ),
        request=ManualEventSerializer,
        responses=AttendanceEventSerializer,
        tags=["Посещаемость"],
    )
    def post(self, request):
        actor = Actor.from_user(request.user)
        payload = validated(ManualEventSerializer, request.data)
        event = AttendanceHrService().manual_event(actor, **payload)
        return Response(
            AttendanceEventSerializer(event).data,
            status=http_status.HTTP_201_CREATED,
        )
