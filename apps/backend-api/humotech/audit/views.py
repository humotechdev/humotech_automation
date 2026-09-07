"""Журнал изменений: только чтение и ничего кроме.

Ни создания, ни правки, ни удаления через API нет и не появится. Записи
кладёт `AuditTrail` из сервисов, в той же транзакции, что и само
изменение. Журнал, который можно отредактировать тем же ключом, которым
делают изменения, ничего не доказывает.

Постраничный вывод здесь по `occurred_at`, а не по `created_at`: у модели
нет `created_at` вовсе — строка не меняется, и второй отметки времени ей
не нужно.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.attendance.views import _date_param, _uuid_param
from humotech.audit.models import AuditLog
from humotech.core.errors import ValidationFailed
from humotech.core.pagination import MAX_PAGE_SIZE, normalize_limit
from humotech.core.rbac import AccessControl, Actor
from humotech.core.timeframes import office_zone, range_bounds

#: Потолок на список идентификаторов в одном запросе. Не техническое
#: ограничение, а защита от запроса длиной в адресную строку: длинный
#: `IN` перестаёт пользоваться индексом и превращается в перебор.
MAX_ENTITY_IDS = 100


class AuditLogSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    action = serializers.CharField()
    entity_type = serializers.CharField()
    entity_id = serializers.UUIDField()
    occurred_at = serializers.DateTimeField()

    actor_user_id = serializers.UUIDField(allow_null=True)
    actor_email = serializers.CharField(
        source="actor_user.email", allow_null=True, default=None
    )
    actor_employee_id = serializers.UUIDField(allow_null=True)

    old_values = serializers.JSONField(allow_null=True)
    new_values = serializers.JSONField(allow_null=True)

    # `ip_address` и `user_agent` в ответе есть: это метаданные запроса,
    # по которым разбирают инциденты. Секретов в них нет — значения
    # проходят через `AuditTrail.sanitize`, который вырезает пароли,
    # токены и хеши ещё при записи.
    ip_address = serializers.CharField(allow_null=True)
    user_agent = serializers.CharField(allow_null=True)


class AuditLogPageSerializer(serializers.Serializer):
    """Страница журнала. Нужна схеме; в коде ответ собирается вручную."""

    items = AuditLogSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class AuditLogView(APIView):
    """Чтение журнала. Требует `audit.read`.

    Метода записи здесь нет намеренно, и это не упущение: единственный
    способ появиться в журнале — быть записанным сервисом, который
    выполняет само действие.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Журнал изменений",
        description=(
            "Только чтение. Метода записи нет намеренно: единственный "
            "способ появиться в журнале — быть записанным сервисом, "
            "который выполняет само действие."
        ),
        parameters=[
            OpenApiParameter(
                "action", str,
                description="Точное действие или префикс, например attendance.",
            ),
            OpenApiParameter(
                "entity_type", str,
                description="Имя таблицы: regions, employees, offices…",
            ),
            OpenApiParameter("entity_id", str),
            OpenApiParameter(
                "entity_ids", str,
                description=(
                    "Несколько идентификаторов через запятую. Нужен там, где "
                    "история одного человека записана под разными "
                    "объектами: назначения роли — отдельные строки, и "
                    "каждое со своим id. Не более "
                    f"{MAX_ENTITY_IDS} за запрос."
                ),
            ),
            OpenApiParameter("actor_user_id", str),
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
        responses=AuditLogPageSerializer,
        tags=["Аудит"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        access = AccessControl()
        access.require(actor, "audit.read")

        queryset = AuditLog.objects.filter(
            organization_id=actor.organization_id
        ).select_related("actor_user")

        action = request.query_params.get("action")
        if action:
            # Префиксом тоже: `attendance.` находит все действия по
            # посещаемости, не заставляя перечислять их поимённо.
            queryset = queryset.filter(action__startswith=action)

        entity_type = request.query_params.get("entity_type")
        if entity_type:
            queryset = queryset.filter(entity_type=entity_type)

        entity_id = _uuid_param(request, "entity_id")
        if entity_id:
            queryset = queryset.filter(entity_id=entity_id)

        entity_ids = _uuid_list(request, "entity_ids")
        if entity_ids is not None:
            # Пустой список — это «ни одного объекта», а не «фильтра нет».
            # Иначе запрос истории человека без назначений вернул бы
            # чужие записи по всей организации.
            queryset = queryset.filter(entity_id__in=entity_ids)

        actor_user_id = _uuid_param(request, "actor_user_id")
        if actor_user_id:
            queryset = queryset.filter(actor_user_id=actor_user_id)

        queryset = self._within_period(request, queryset, actor)

        limit = normalize_limit(_limit(request))
        cursor = request.query_params.get("cursor")
        if cursor:
            position = _decode(cursor)
            queryset = queryset.filter(
                Q(occurred_at__lt=position[0])
                | Q(occurred_at=position[0], id__lt=position[1])
            )

        rows = list(queryset.order_by("-occurred_at", "-id")[: limit + 1])
        has_more = len(rows) > limit
        items = rows[:limit]

        return Response(
            {
                "items": AuditLogSerializer(items, many=True).data,
                "next_cursor": (
                    _encode(items[-1].occurred_at, items[-1].id)
                    if has_more and items
                    else None
                ),
                "has_more": has_more,
            }
        )

    @staticmethod
    def _within_period(request, queryset, actor: Actor):
        first = _date_param(request, "date_from")
        last = _date_param(request, "date_to")
        if not first and not last:
            return queryset
        if first and last and last < first:
            raise ValidationFailed(
                "Конец периода раньше начала",
                details={"date_from": first.isoformat(),
                         "date_to": last.isoformat()},
            )
        from humotech.offices.models import Office

        office = (
            Office.objects.filter(organization_id=actor.organization_id)
            .order_by("created_at")
            .first()
        )
        start, end = range_bounds(first or last, last or first, office_zone(office))
        return queryset.filter(occurred_at__gte=start, occurred_at__lt=end)


def _uuid_list(request, name: str) -> list[uuid.UUID] | None:
    """Список идентификаторов через запятую. Отсутствие ≠ пустой список."""
    raw = request.query_params.get(name)
    if raw is None:
        return None
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) > MAX_ENTITY_IDS:
        raise ValidationFailed(
            f"Параметр «{name}» принимает не более {MAX_ENTITY_IDS} "
            "идентификаторов",
            details={"field": name, "count": len(parts)},
        )
    try:
        return [uuid.UUID(part) for part in parts]
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть списком UUID через запятую",
            details={"field": name, "value": raw},
        ) from exc


def _limit(request) -> int | None:
    raw = request.query_params.get("limit")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(
            "Параметр «limit» должен быть числом", details={"limit": raw}
        ) from exc
    if value < 1 or value > MAX_PAGE_SIZE:
        raise ValidationFailed(
            f"Параметр «limit» должен быть от 1 до {MAX_PAGE_SIZE}",
            details={"limit": value},
        )
    return value


def _encode(moment: datetime, row_id: uuid.UUID) -> str:
    payload = json.dumps({"at": moment.isoformat(), "id": str(row_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode(raw: str) -> tuple[datetime, uuid.UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return datetime.fromisoformat(payload["at"]), uuid.UUID(payload["id"])
    except (binascii.Error, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as exc:
        raise ValidationFailed(
            "Некорректный курсор постраничного вывода", details={"cursor": raw}
        ) from exc
