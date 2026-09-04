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

from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.attendance.hr import AttendanceHrService
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

    def post(self, request):
        actor = Actor.from_user(request.user)
        payload = validated(ManualEventSerializer, request.data)
        event = AttendanceHrService().manual_event(actor, **payload)
        return Response(
            AttendanceEventSerializer(event).data,
            status=http_status.HTTP_201_CREATED,
        )
