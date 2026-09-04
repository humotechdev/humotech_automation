"""REST-интерфейс очереди уведомлений.

Только чтение и три управляющих действия. Создать уведомление через HTTP
нельзя: строка заводится той же транзакцией, что и событие (см.
`outbox.enqueue`), и ручное создание означало бы сообщение о том, чего
не было.
"""

from __future__ import annotations

import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.attendance.serializers import EmployeeBriefSerializer
from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import NOTIFICATION_CHANNELS, NOTIFICATION_STATUSES
from humotech.core.errors import ValidationFailed
from humotech.notifications.service import MAX_BULK_RETRY, NotificationService


class NotificationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    employee_id = serializers.UUIDField()
    employee = EmployeeBriefSerializer()
    channel = serializers.ChoiceField(choices=NOTIFICATION_CHANNELS)
    notification_type = serializers.CharField()
    title = serializers.CharField(allow_null=True)
    body = serializers.CharField()
    status = serializers.ChoiceField(choices=NOTIFICATION_STATUSES)
    attempts = serializers.IntegerField()
    error_message = serializers.CharField(
        allow_null=True,
        help_text="Причина последней неудачи в безопасном для журнала виде",
    )
    scheduled_at = serializers.DateTimeField(allow_null=True)
    next_attempt_at = serializers.DateTimeField(
        allow_null=True, help_text="Когда очередь возьмёт строку снова"
    )
    sent_at = serializers.DateTimeField(allow_null=True)
    read_at = serializers.DateTimeField(allow_null=True)
    related_entity_type = serializers.CharField(allow_null=True)
    related_entity_id = serializers.UUIDField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class NotificationPageSerializer(serializers.Serializer):
    items = NotificationSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class RetryFailedSerializer(serializers.Serializer):
    employee_id = serializers.UUIDField(required=False, allow_null=True)
    notification_type = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Префикс типа, например «absence.» или «telegram.link.»",
    )


class RetryFailedResultSerializer(serializers.Serializer):
    requeued = serializers.IntegerField(
        help_text="Сколько уведомлений вернулось в очередь"
    )


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


@extend_schema(tags=["Уведомления"])
class NotificationViewSet(ServiceViewSet):
    """Очередь отправки сообщений сотрудникам.

    Чтение требует `notifications.read`, управление — `notifications.manage`.
    Видны уведомления сотрудников из области видимости пользователя.
    """

    service_class = NotificationService
    read_serializer_class = NotificationSerializer

    @extend_schema(
        summary="Список уведомлений",
        parameters=[
            OpenApiParameter(
                "employee_id", OpenApiTypes.UUID,
                description="Уведомления одного сотрудника",
            ),
            OpenApiParameter("status", str, enum=list(NOTIFICATION_STATUSES)),
            OpenApiParameter("channel", str, enum=list(NOTIFICATION_CHANNELS)),
            OpenApiParameter(
                "notification_type", str,
                description="Префикс типа, например «absence.»",
            ),
            OpenApiParameter(
                "search", str, description="Подстрока в заголовке или тексте",
            ),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
        responses={200: NotificationPageSerializer},
    )
    def list(self, request):
        params = self.list_params()
        return self.page_response(
            self.service.list(
                self.actor,
                **params,
                employee_id=_uuid(request, "employee_id"),
                channel=request.query_params.get("channel") or None,
                notification_type=(
                    request.query_params.get("notification_type") or None
                ),
            )
        )

    @extend_schema(summary="Одно уведомление")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    @extend_schema(
        summary="Отправить повторно",
        description=(
            "Возвращает уведомление в очередь и обнуляет счётчик попыток: "
            "повтор нажимают, когда причину неудачи уже устранили. "
            "Работает из состояний FAILED и CANCELLED — отправленное "
            "повторить нельзя, оно уже в чате у человека."
        ),
        request=None,
        responses={200: NotificationSerializer},
    )
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        return self.item_response(self.service.retry(self.actor, pk))

    @extend_schema(
        summary="Снять с отправки",
        description=(
            "Работает из состояний PENDING и FAILED. Строку, которую "
            "прямо сейчас держит отправщик, снять нельзя: «отменено» "
            "рядом с уходящим сообщением было бы неправдой."
        ),
        request=None,
        responses={200: NotificationSerializer},
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self.item_response(self.service.cancel(self.actor, pk))

    @extend_schema(
        summary="Повторить всё, что упало",
        description=(
            "Массовый повтор только для FAILED: отмена — решение человека, "
            f"и снимать его пачкой нельзя. За раз не больше {MAX_BULK_RETRY} "
            "уведомлений."
        ),
        request=RetryFailedSerializer,
        responses={200: RetryFailedResultSerializer},
        examples=[
            OpenApiExample(
                "Только заявки на отсутствие",
                value={"notification_type": "absence."},
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="retry-failed")
    def retry_failed(self, request):
        payload = validated(RetryFailedSerializer, request.data)
        return Response(
            {"requeued": self.service.retry_failed(self.actor, **payload)}
        )


__all__ = ["NotificationViewSet"]
