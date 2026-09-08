"""Общая очередь заявок HR: отсутствия и исправления отметок вместе.

Зачем отдельный адрес, а не склейка двух списков на клиенте: у каждого
из них своя страница. Взять по десять свежих из двух очередей и сложить
— значит получить не десять самых свежих заявок, а произвольную смесь,
в которой часть новых записей не показана вовсе.

Слияние здесь честное: обе очереди отсортированы по одному ключу
`(created_at, id)` и курсор один на обе. Он означает позицию в общем
порядке, применяется к каждому потоку отдельно, и дальше два
отсортированных ряда сливаются как в сортировке слиянием.

Своих правил доступа у этого адреса нет: обе выборки собираются теми же
сервисами с их проверками прав и областью видимости. Права на чтение
нужны оба — иначе половина очереди молча пропала бы, а человек считал бы,
что заявок нет.
"""

from __future__ import annotations

from dataclasses import dataclass

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.absences.services import AbsenceService
from humotech.absences.views import hr_request_json
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.serializers import CorrectionRequestSerializer
from humotech.attendance.views import _uuid_param
from humotech.core.pagination import Cursor, normalize_limit
from humotech.core.rbac import Actor


class QueueItemSerializer(serializers.Serializer):
    """Строка очереди. `kind` говорит, чем именно является запись."""

    kind = serializers.ChoiceField(choices=["absence", "correction"])
    id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    absence = serializers.DictField(required=False)
    correction = serializers.DictField(required=False)


class QueueResponseSerializer(serializers.Serializer):
    items = QueueItemSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


@dataclass(frozen=True)
class _Row:
    """Общий вид записи на время слияния."""

    created_at: object
    id: object
    kind: str
    body: dict

    @property
    def order(self) -> tuple:
        # Тот же ключ, по которому страницы отдают оба сервиса.
        return (self.created_at, str(self.id))


class RequestQueueView(APIView):
    """Очередь заявок HR: отсутствия и исправления отметок в одном порядке."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="requests_queue",
        summary="Общая очередь заявок HR",
        description=(
            "Отсутствия и исправления отметок одним списком, отсортированным "
            "по времени подачи. Курсор общий: он указывает позицию в этом "
            "порядке, а не в одной из двух очередей."
        ),
        parameters=[
            OpenApiParameter("kind", str, enum=["absence", "correction"]),
            OpenApiParameter("status", str),
            OpenApiParameter(
                "employee_id", str,
                description=(
                    "Заявки одного сотрудника. Фильтр сужает уже "
                    "разрешённое: чужой сотрудник даёт пустой набор, "
                    "а не чужие заявки"
                ),
            ),
            OpenApiParameter("office_id", str),
            OpenApiParameter("region_id", str),
            OpenApiParameter("search", str),
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
            OpenApiParameter("limit", int),
            OpenApiParameter("cursor", str),
        ],
        responses=QueueResponseSerializer,
        tags=["Заявки"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        size = normalize_limit(_int_param(request, "limit"))
        kind = request.query_params.get("kind") or None
        position = _position(request.query_params.get("cursor"))

        rows: list[_Row] = []
        if kind in (None, "absence"):
            rows += self._absences(actor, request, position, size)
        if kind in (None, "correction"):
            rows += self._corrections(actor, request, position, size)

        rows.sort(key=lambda row: row.order, reverse=True)
        page = rows[:size]
        has_more = len(rows) > size

        return Response(
            {
                "items": [
                    {
                        "kind": row.kind,
                        "id": str(row.id),
                        "created_at": row.created_at,
                        row.kind: row.body,
                    }
                    for row in page
                ],
                "next_cursor": (
                    Cursor(created_at=page[-1].created_at, id=page[-1].id).encode()
                    if page and has_more
                    else None
                ),
                "has_more": has_more,
            }
        )

    # ----------------------------------------------------------- источники

    def _absences(self, actor, request, position, size) -> list[_Row]:
        queryset = AbsenceService().queue(
            actor,
            status=request.query_params.get("status") or None,
            type_code=request.query_params.get("type") or None,
            employee_id=_uuid_param(request, "employee_id"),
            office_id=_uuid_param(request, "office_id"),
            region_id=_uuid_param(request, "region_id"),
            search=request.query_params.get("search") or None,
            date_from=request.query_params.get("date_from") or None,
            date_to=request.query_params.get("date_to") or None,
        )
        rows = list(_before(queryset, position)[: size + 1])
        return [
            _Row(row.created_at, row.id, "absence", hr_request_json(row)) for row in rows
        ]

    def _corrections(self, actor, request, position, size) -> list[_Row]:
        queryset = AttendanceHrService().correction_queue(
            actor,
            status=request.query_params.get("status") or None,
            employee_id=_uuid_param(request, "employee_id"),
            office_id=_uuid_param(request, "office_id"),
            region_id=_uuid_param(request, "region_id"),
            search=request.query_params.get("search") or None,
        )
        rows = list(_before(queryset, position)[: size + 1])
        serialized = CorrectionRequestSerializer(rows, many=True).data
        return [
            _Row(row.created_at, row.id, "correction", dict(body))
            for row, body in zip(rows, serialized)
        ]


def _before(queryset, position):
    """Строго раньше позиции курсора — тем же сравнением, что в `paginate`."""
    from django.db.models import Q

    queryset = queryset.order_by("-created_at", "-id")
    if position is None:
        return queryset
    return queryset.filter(
        Q(created_at__lt=position.created_at)
        | Q(created_at=position.created_at, id__lt=position.id)
    )


def _position(cursor: str | None):
    return Cursor.decode(cursor) if cursor else None


def _int_param(request, name: str) -> int | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


__all__ = ["RequestQueueView"]
