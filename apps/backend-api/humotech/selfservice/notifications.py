"""Уведомления сотрудника: лента и отметка о прочтении.

Что здесь показывается — только ДОШЕДШЕЕ. Строка в очереди ещё не
сообщение: она может не уйти вовсе, а увидев её в приложении, человек
решит, что уже предупреждён. Поэтому берутся `SENT` и `READ`, а
`PENDING`, `RUNNING`, `FAILED` и `CANCELLED` не берутся никогда.

Собственной модели «объявление» в системе нет, и здесь она не заводится.
`Notification` адресована конкретному сотруднику: рассылка на офис,
регион или организацию превращается в строки по людям на стороне
отправителя. Это значит, что адресность здесь не вычисляется и
подделать её нечем — сотрудник видит свои строки и только их.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.response import Response

from humotech.core.errors import NotFound
from humotech.notifications.models import Notification
from humotech.selfservice.views import EmployeeSelfView

#: Состояния, в которых уведомление уже у человека.
DELIVERED = ("SENT", "READ")

#: Сколько строк отдаём за раз. Лента на главном экране показывает одну,
#: экран уведомлений — десяток; больше на телефоне всё равно не читают.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50


class EmployeeNotificationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    notification_type = serializers.CharField()
    title = serializers.CharField(allow_null=True)
    body = serializers.CharField()
    sent_at = serializers.DateTimeField(allow_null=True)
    read_at = serializers.DateTimeField(allow_null=True)
    is_read = serializers.BooleanField()


class EmployeeNotificationFeedSerializer(serializers.Serializer):
    unread = serializers.IntegerField()
    items = EmployeeNotificationSerializer(many=True)


def _limit(raw: str | None) -> int:
    """Размер ленты из запроса. Мусор — это значение по умолчанию, а не 500.

    `int("abc")` падал исключением, и любой кривой параметр от клиента
    превращался в ошибку сервера. Только ASCII-цифры и не длиннее трёх
    знаков: `"²".isdigit()` истинно, а `int("²")` падает.
    """
    if not raw or not raw.isascii() or not raw.isdigit() or len(raw) > 3:
        return DEFAULT_LIMIT
    return min(max(int(raw), 1), MAX_LIMIT)


def _json(row: Notification) -> dict:
    return {
        "id": str(row.id),
        "notification_type": row.notification_type,
        "title": row.title,
        "body": row.body,
        # Время отправки, а не заведения строки: человек получил её тогда.
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "is_read": row.read_at is not None,
    }


class NotificationListView(EmployeeSelfView):
    """Мои уведомления: что дошло и сколько из этого не прочитано."""

    @extend_schema(
        operation_id="me_notifications",
        summary="Мои уведомления",
        parameters=[
            OpenApiParameter(
                "limit", OpenApiTypes.INT, description=f"не больше {MAX_LIMIT}"
            )
        ],
        responses={200: EmployeeNotificationFeedSerializer},
    )
    def get(self, request):
        limit = _limit(request.query_params.get("limit"))
        mine = Notification.objects.filter(
            employee_id=self.context.employee.id,
            organization_id=self.context.organization_id,
            status__in=DELIVERED,
        )
        rows = list(mine.order_by("-created_at")[:limit])
        return Response(
            {
                # Счётчик считается по всей ленте, а не по отданной
                # странице: иначе «непрочитанных 3» означало бы «три
                # среди двадцати последних», а это другое утверждение.
                "unread": mine.filter(read_at__isnull=True).count(),
                "items": [_json(row) for row in rows],
            }
        )


class NotificationReadView(EmployeeSelfView):
    """Отметка «прочитано». Повтор ничего не меняет и не считается ошибкой."""

    @extend_schema(
        operation_id="me_notification_read",
        summary="Отметить уведомление прочитанным",
        request=None,
        responses={200: EmployeeNotificationSerializer},
    )
    def post(self, request, notification_id):
        row = (
            Notification.objects.filter(
                id=notification_id,
                employee_id=self.context.employee.id,
                organization_id=self.context.organization_id,
                status__in=DELIVERED,
            )
            .order_by()
            .first()
        )
        if row is None:
            # Чужое уведомление и несуществующее отвечают одинаково:
            # разница ответов позволяла бы перебором узнать, какие
            # идентификаторы существуют.
            raise NotFound("Уведомление не найдено")

        if row.read_at is None:
            row.read_at = timezone.now()
            # Статус меняется вместе с отметкой: у кадровика вкладка
            # «Отправлено» показывает SENT и READ вместе, поэтому строка
            # со своего места не исчезает.
            row.status = "READ"
            row.save(update_fields=["read_at", "status", "updated_at"])

        return Response(_json(row))


__all__ = ["NotificationListView", "NotificationReadView"]
