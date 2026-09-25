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
from datetime import date

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.absences.services import AbsenceService
from humotech.absences.views import hr_request_json
from humotech.core.timeframes import organization_zone
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.serializers import CorrectionRequestSerializer
from humotech.attendance.views import _uuid_param
from humotech.core.pagination import Cursor, normalize_limit
from humotech.core.rbac import Actor
from humotech.employees.models import EmployeeAssignment
from humotech.employees.services import current_primary_assignment_filter

# Заявка, по которой ещё не приняли решение. Одинаково у отсутствий и у
# исправлений отметок: статусы у обеих моделей одни и те же.
OPEN_STATUSES = "SUBMITTED,IN_REVIEW"
LEAVE_TYPES = "ANNUAL_LEAVE,UNPAID_LEAVE"
# Заявка «на сам отпуск» — создание или продление. Отмена — отдельная вкладка.
OWN_KINDS = "CREATE,EXTEND"


class QueueItemSerializer(serializers.Serializer):
    """Строка очереди. `kind` говорит, чем именно является запись."""

    kind = serializers.ChoiceField(choices=["absence", "correction"])
    id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    place = serializers.DictField(
        allow_null=True,
        help_text="Где сотрудник работает сейчас: office_name, department_name",
    )
    absence = serializers.DictField(required=False)
    correction = serializers.DictField(required=False)


class QueueCountsSerializer(serializers.Serializer):
    open = serializers.IntegerField(help_text="Ждут решения: отсутствия и исправления")
    all = serializers.IntegerField()
    leave = serializers.IntegerField(help_text="Отпуска: создание и продление")
    sick = serializers.IntegerField(help_text="Больничные: создание и продление")
    fixes = serializers.IntegerField(help_text="Исправления отметок")
    cancel = serializers.IntegerField(help_text="Заявки на отмену отсутствия")


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
            OpenApiParameter(
                "request_kind", str,
                description="Вид заявки на отсутствие: CREATE, EXTEND, CANCEL",
            ),
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
        places = _places(
            {str(row.body["employee"]["id"]) for row in page if row.body.get("employee")}
        )

        return Response(
            {
                "items": [
                    {
                        "kind": row.kind,
                        "id": str(row.id),
                        "created_at": row.created_at,
                        "place": places.get(str((row.body.get("employee") or {}).get("id"))),
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
            request_kind=request.query_params.get("request_kind") or None,
        )
        rows = list(_before(queryset, position)[: size + 1])
        # Пояс показа считается один раз на страницу: он один на всю
        # организацию, а запрос за ним — не бесплатный.
        zone = organization_zone(actor.organization_id)
        return [
            _Row(row.created_at, row.id, "absence", hr_request_json(row, zone))
            for row in rows
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


class RequestCountsView(APIView):
    """Счётчики вкладок очереди одним ответом.

    Фильтры те же, что у списка, кроме статуса, вида и типа — это и есть
    оси вкладок. Права те же: без права на один из потоков отказ, а не
    молча урезанные числа, которые читались бы как «заявок нет».
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="requests_queue_counts",
        summary="Счётчики вкладок очереди заявок",
        parameters=[
            OpenApiParameter("employee_id", str),
            OpenApiParameter("office_id", str),
            OpenApiParameter("region_id", str),
            OpenApiParameter("search", str),
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
        ],
        responses=QueueCountsSerializer,
        tags=["Заявки"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        common = {
            "employee_id": _uuid_param(request, "employee_id"),
            "office_id": _uuid_param(request, "office_id"),
            "region_id": _uuid_param(request, "region_id"),
            "search": request.query_params.get("search") or None,
        }
        dates = {
            "date_from": request.query_params.get("date_from") or None,
            "date_to": request.query_params.get("date_to") or None,
        }
        absences = AbsenceService()
        corrections = AttendanceHrService()

        def absence(**extra) -> int:
            return absences.queue(actor, **common, **dates, **extra).count()

        def correction(**extra) -> int:
            return corrections.correction_queue(actor, **common, **extra).count()

        return Response(
            {
                "open": absence(status=OPEN_STATUSES) + correction(status=OPEN_STATUSES),
                "all": absence() + correction(),
                "leave": absence(type_code=LEAVE_TYPES, request_kind=OWN_KINDS),
                "sick": absence(type_code="SICK_LEAVE", request_kind=OWN_KINDS),
                "fixes": correction(),
                "cancel": absence(request_kind="CANCEL"),
            }
        )


def _places(employee_ids: set[str]) -> dict[str, dict]:
    """Офис и отдел по текущему основному назначению — одним запросом.

    Место берётся на сегодня, а не на дату заявки: кадровик ищет человека
    там, где тот работает сейчас. Нет назначения — нет и места (`None`),
    а не выдуманный «главный офис».
    """
    if not employee_ids:
        return {}
    rows = (
        EmployeeAssignment.objects.filter(
            current_primary_assignment_filter(date.today()),
            employee_id__in=employee_ids,
        )
        .select_related("office", "department")
    )
    return {
        str(row.employee_id): {
            "office_name": row.office.name if row.office_id else None,
            "department_name": row.department.name if row.department_id else None,
        }
        for row in rows
    }


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


__all__ = ["RequestCountsView", "RequestQueueView"]
