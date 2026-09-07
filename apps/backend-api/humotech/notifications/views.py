"""REST-интерфейс очереди уведомлений.

Только чтение и три управляющих действия. Создать уведомление через HTTP
нельзя: строка заводится той же транзакцией, что и событие (см.
`outbox.enqueue`), и ручное создание означало бы сообщение о том, чего
не было.
"""

from __future__ import annotations

import uuid
from datetime import date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.attendance.serializers import EmployeeBriefSerializer
from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import (
    NOTIFICATION_ATTEMPT_OUTCOMES,
    NOTIFICATION_CHANNELS,
    NOTIFICATION_STATUSES,
)
from humotech.core.errors import ValidationFailed
from humotech.notifications.service import (
    CANCELLABLE,
    MAX_BULK_RETRY,
    RETRYABLE,
    NotificationService,
)


class NotificationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    employee_id = serializers.UUIDField()
    employee = EmployeeBriefSerializer()
    office_id = serializers.UUIDField(
        source="office_at_id",
        allow_null=True,
        help_text=(
            "Офис получателя НА МОМЕНТ уведомления: основное назначение, "
            "чей период содержит день создания. Не сегодняшний офис "
            "сотрудника — он мог с тех пор перейти в другой."
        ),
    )
    office_name = serializers.CharField(source="office_at_name", allow_null=True)
    region_name = serializers.CharField(source="region_at_name", allow_null=True)
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
    can_retry = serializers.SerializerMethodField(
        help_text="Разрешает ли ТЕКУЩЕЕ состояние вернуть строку в очередь"
    )
    can_cancel = serializers.SerializerMethodField()

    def get_can_retry(self, row) -> bool:
        return row.status in RETRYABLE

    def get_can_cancel(self, row) -> bool:
        return row.status in CANCELLABLE


class NotificationPageSerializer(serializers.Serializer):
    items = NotificationSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class NotificationCountsSerializer(serializers.Serializer):
    """Сводка по всему доступному набору."""

    total = serializers.IntegerField()
    PENDING = serializers.IntegerField()
    RUNNING = serializers.IntegerField()
    SENT = serializers.IntegerField()
    FAILED = serializers.IntegerField()
    CANCELLED = serializers.IntegerField()
    READ = serializers.IntegerField()
    sent = serializers.IntegerField(help_text="SENT и READ вместе")
    queued = serializers.IntegerField(help_text="PENDING и RUNNING вместе")
    failed = serializers.IntegerField()
    cancelled = serializers.IntegerField()
    timezone = serializers.CharField(
        help_text="Пояс организации: в нём показывается время и режется период"
    )


class NotificationAttemptSerializer(serializers.Serializer):
    """Одна СОСТОЯВШАЯСЯ попытка отправки."""

    number = serializers.IntegerField()
    attempted_at = serializers.DateTimeField()
    outcome = serializers.ChoiceField(choices=NOTIFICATION_ATTEMPT_OUTCOMES)
    reason = serializers.CharField(
        allow_null=True,
        help_text=(
            "Короткий код причины, а не ответ Telegram: ответ может "
            "содержать эхо запроса, то есть текст уведомления целиком"
        ),
    )


class NotificationAttemptsSerializer(serializers.Serializer):
    items = NotificationAttemptSerializer(many=True)
    kept = serializers.BooleanField(
        help_text=(
            "Велась ли история для этой строки. false означает, что "
            "уведомление старше самой истории: попытки были, но их "
            "времена не сохранялись и восстановить их неоткуда"
        )
    )


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


def _day(request, name: str) -> date | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть датой ГГГГ-ММ-ДД",
            details={"field": name},
        ) from exc


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
            OpenApiParameter(
                "status", str,
                description=(
                    "Одно состояние или несколько через запятую: вкладка "
                    "«Отправлено» — это SENT и READ сразу"
                ),
            ),
            OpenApiParameter("channel", str, enum=[*NOTIFICATION_CHANNELS]),
            OpenApiParameter("office_id", OpenApiTypes.UUID),
            OpenApiParameter("region_id", OpenApiTypes.UUID),
            OpenApiParameter(
                "date_from", OpenApiTypes.DATE,
                description="Начало периода по дню создания, в поясе организации",
            ),
            OpenApiParameter("date_to", OpenApiTypes.DATE),
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
            self.service.list(self.actor, **params, **self._filters(request))
        )

    @extend_schema(
        summary="Сводка по состояниям",
        description=(
            "Считается по всему доступному набору и с теми же фильтрами "
            "периода, области и поиска, что и список. Фильтр состояния "
            "сюда не передаётся: число рядом с вкладкой не должно "
            "зависеть от открытой вкладки.\n\n"
            "Здесь же приходит пояс организации — в нём показывается "
            "время и режется период."
        ),
        parameters=[
            OpenApiParameter("employee_id", OpenApiTypes.UUID),
            OpenApiParameter("channel", str, enum=[*NOTIFICATION_CHANNELS]),
            OpenApiParameter("notification_type", str),
            OpenApiParameter("search", str),
            OpenApiParameter("office_id", OpenApiTypes.UUID),
            OpenApiParameter("region_id", OpenApiTypes.UUID),
            OpenApiParameter("date_from", OpenApiTypes.DATE),
            OpenApiParameter("date_to", OpenApiTypes.DATE),
        ],
        responses={200: NotificationCountsSerializer},
    )
    @action(detail=False)
    def counts(self, request):
        return Response(
            self.service.counts(
                self.actor,
                search=request.query_params.get("search") or None,
                **self._filters(request),
            )
        )

    @extend_schema(summary="Одно уведомление")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    @extend_schema(
        summary="История попыток отправки",
        description=(
            "Только СОСТОЯВШИЕСЯ попытки: каждая запись сделана в тот "
            "момент, когда попытка кончилась. Ручной повтор сюда не "
            "попадает — он не попытка отправки, а решение человека, "
            "и остаётся в журнале действий.\n\n"
            "У уведомлений старше самой истории записей нет: `kept` "
            "равно false, и придумывать им времена попыток нельзя."
        ),
        responses={200: NotificationAttemptsSerializer},
    )
    @action(detail=True)
    def attempts(self, request, pk=None):
        rows = self.service.attempts(self.actor, pk)
        row = self.service.get(self.actor, pk)
        return Response(
            {
                "items": NotificationAttemptSerializer(rows, many=True).data,
                # Пусто при ненулевом счётчике означает не «попыток не
                # было», а «их не записывали»: разница для разбора
                # существенная, и решать её должен сервер.
                "kept": bool(rows) or (row.attempts == 0 and row.sent_at is None),
            }
        )

    def _filters(self, request) -> dict:
        return {
            "employee_id": _uuid(request, "employee_id"),
            "office_id": _uuid(request, "office_id"),
            "region_id": _uuid(request, "region_id"),
            "channel": request.query_params.get("channel") or None,
            "notification_type": (
                request.query_params.get("notification_type") or None
            ),
            "date_from": _day(request, "date_from"),
            "date_to": _day(request, "date_to"),
        }

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
