"""REST-интерфейс привязки Telegram.

Три разных входа, у каждого свой способ доказать, кто обращается:

  * **HR** — сессия CRM. Права и область считает сервис, view их не трогает;
  * **бот** — общий секрет в заголовке. Пользователя за ним нет: право
    выполнить операцию даёт токен приглашения, а секрет подтверждает,
    что `telegram_user_id` пришёл от Telegram через нашего бота;
  * **Mini App** — внутренний токен, выданный в обмен на `initData`.
    Класс аутентификации подключён ТОЛЬКО к своим view: токеном Mini App
    невозможно обратиться к кадровому API даже при ошибке в маршрутах.

Ограничение частоты стоит на всех трёх, но по разным причинам: у HR — против
массовой рассылки ссылок, у бота — против шквала переходов, у Mini App —
против перебора подписи.
"""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from humotech.core.api import ServiceViewSet, validated
from humotech.core.rbac import Actor
from humotech.telegram.auth import (
    IsLinkedEmployee,
    IsTelegramBot,
    MiniAppAuthentication,
)
from humotech.telegram.serializers import (
    AccountSerializer,
    BotConsumeSerializer,
    InvitationCreateSerializer,
    InvitationSerializer,
    IssuedInvitationSerializer,
    LinkStatusSerializer,
    MiniAppAuthSerializer,
    MiniAppSessionSerializer,
)
from humotech.telegram.services import TelegramLinkService, TelegramMiniAppService


class TelegramInvitationViewSet(ServiceViewSet):
    """Ссылки привязки: выдача, отзыв и решение HR."""

    service_class = TelegramLinkService
    read_serializer_class = InvitationSerializer

    def get_throttles(self):
        # Ограничение только на выдачу ссылок: чтение списка ограничивать
        # незачем, а массовая рассылка ссылок — не рабочий сценарий.
        if self.action == "create":
            self.throttle_scope = "telegram_invitations"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def list(self, request):
        params = self.list_params()
        params.pop("search", None)
        employee_id = request.query_params.get("employee_id")
        if employee_id:
            params["employee_id"] = employee_id
        return self.page_response(self.service.list_invitations(self.actor, **params))

    @action(detail=False, methods=["get"])
    def pending(self, request):
        """Переходы, ожидающие решения HR."""
        params = self.list_params()
        return self.page_response(
            self.service.pending(
                self.actor, limit=params.get("limit"), cursor=params.get("cursor")
            )
        )

    def create(self, request):
        payload = validated(InvitationCreateSerializer, request.data)
        issued = self.service.create_invitation(self.actor, payload["employee_id"])
        # Единственный ответ, в котором есть открытый токен. Повторно
        # получить его нельзя: в базе только хеш.
        return Response(
            IssuedInvitationSerializer(issued).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        account = self.service.confirm(self.actor, pk)
        return Response(AccountSerializer(account).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        account = self.service.reject(self.actor, pk)
        return Response(
            AccountSerializer(account).data if account else {},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        return self.item_response(self.service.revoke_invitation(self.actor, pk))


class BotLinkResultSerializer(serializers.Serializer):
    """Ответ боту. Карточки сотрудника здесь нет намеренно.

    Сотрудник боту неизвестен и знать его боту незачем: он только
    сообщает человеку, что привязка ждёт подтверждения.
    """

    status = serializers.CharField()
    employee_known = serializers.BooleanField()


class MiniAppEmployeeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    employee_number = serializers.CharField(allow_null=True)
    employment_status = serializers.CharField()
    preferred_language = serializers.CharField(allow_null=True)


class MiniAppTelegramSerializer(serializers.Serializer):
    status = serializers.CharField()
    username = serializers.CharField(allow_null=True)


class MiniAppMeSerializer(serializers.Serializer):
    employee = MiniAppEmployeeSerializer()
    telegram = MiniAppTelegramSerializer()


class OutboxMessageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    chat_id = serializers.IntegerField()
    text = serializers.CharField()
    type = serializers.CharField()
    attempts = serializers.IntegerField()


class OutboxBatchSerializer(serializers.Serializer):
    messages = OutboxMessageSerializer(many=True)
    reclaimed = serializers.IntegerField(
        help_text=(
            "Сколько строк вернулось в очередь после падения отправщика. "
            "Без этой уборки они не ушли бы никогда"
        ),
    )


class OutboxResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    sent = serializers.BooleanField()
    error = serializers.CharField(
        required=False, allow_blank=True, allow_null=True,
        help_text=(
            "Только код причины. Текст ошибки Telegram может содержать "
            "эхо запроса, то есть само уведомление"
        ),
    )


class OutboxReportSerializer(serializers.Serializer):
    results = OutboxResultSerializer(many=True)


class OutboxAcceptedSerializer(serializers.Serializer):
    accepted = serializers.IntegerField()


class _EmployeeScopedView(APIView):
    permission_classes = [IsAuthenticated]

    @property
    def actor(self) -> Actor:
        return Actor.from_user(self.request.user)


@extend_schema(
    tags=["Telegram"],
    parameters=[
        OpenApiParameter(
            "employee_pk", OpenApiTypes.UUID, location=OpenApiParameter.PATH
        ),
    ],
)
class EmployeeTelegramView(_EmployeeScopedView):
    """Состояние привязки в карточке сотрудника."""

    @extend_schema(
        operation_id="employee_telegram_status",
        summary="Привязка Telegram у сотрудника",
        responses={200: LinkStatusSerializer},
    )
    def get(self, request, employee_pk):
        link = TelegramLinkService().status(self.actor, employee_pk)
        return Response(LinkStatusSerializer(link).data)


@extend_schema(
    tags=["Telegram"],
    parameters=[
        OpenApiParameter(
            "employee_pk", OpenApiTypes.UUID, location=OpenApiParameter.PATH
        ),
    ],
)
class EmployeeTelegramDisconnectView(_EmployeeScopedView):
    """Отключение привязки.

    Отдельный адрес, а не правка статуса: обратной операции у него нет —
    вернуть доступ можно только новой ссылкой и новым подтверждением.
    """

    @extend_schema(
        operation_id="employee_telegram_disconnect",
        summary="Отключить Telegram сотруднику",
        description=(
            "Обратной операции нет: вернуть доступ можно только новой "
            "ссылкой и новым подтверждением."
        ),
        request=None,
        responses={200: AccountSerializer},
    )
    def post(self, request, employee_pk):
        account = TelegramLinkService().disconnect(self.actor, employee_pk)
        return Response(AccountSerializer(account).data)


class BotLinkView(APIView):
    """Погашение ссылки. Вызывает только бот, пользователя за запросом нет."""

    authentication_classes: list = []
    permission_classes = [IsTelegramBot]
    throttle_scope = "telegram_bot_link"
    throttle_classes = [ScopedRateThrottle]

    @extend_schema(
        operation_id="telegram_bot_link",
        summary="Погашение ссылки привязки",
        description=(
            "Вызывает только бот, предъявляя общий секрет. Пользователя "
            "за запросом нет: право на операцию даёт токен приглашения."
        ),
        request=BotConsumeSerializer,
        responses={201: BotLinkResultSerializer},
        tags=["Telegram"],
    )
    def post(self, request):
        payload = validated(BotConsumeSerializer, request.data)
        account = TelegramLinkService().consume(
            token=payload["token"],
            telegram_user_id=payload["telegram_user_id"],
            telegram_chat_id=payload["telegram_chat_id"],
            telegram_username=payload.get("telegram_username") or None,
            language_code=payload.get("language_code") or None,
        )
        # Бот получает статус, а не карточку: сотрудник ему неизвестен
        # и знать его боту незачем — он только сообщает человеку,
        # что привязка ждёт подтверждения.
        return Response(
            {"status": account.status, "employee_known": True},
            status=status.HTTP_201_CREATED,
        )


class MiniAppAuthView(APIView):
    """Обмен `initData` Telegram на внутренний токен HUMOTECH."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "telegram_mini_app"
    throttle_classes = [ScopedRateThrottle]

    @extend_schema(
        operation_id="telegram_mini_app_auth",
        summary="Обмен initData на токен",
        description=(
            "Подпись Telegram проверяется на сервере. Состояние привязки "
            "перечитывается на каждом последующем запросе, поэтому отзыв "
            "действует немедленно, а не с истечением срока токена."
        ),
        request=MiniAppAuthSerializer,
        responses={200: MiniAppSessionSerializer},
        tags=["Telegram"],
    )
    def post(self, request):
        payload = validated(MiniAppAuthSerializer, request.data)
        session = TelegramMiniAppService().authenticate(payload["init_data"])
        return Response(MiniAppSessionSerializer(session).data)


class MiniAppMeView(APIView):
    """Защищённая заглушка личного кабинета.

    Полный кабинет — следующий этап. Здесь проверяется главное: токен
    работает, привязка жива, и сотрудник получает ровно свои данные.
    """

    authentication_classes = [MiniAppAuthentication]
    permission_classes = [IsLinkedEmployee]

    @extend_schema(
        operation_id="telegram_mini_app_me",
        summary="Кто открыл Mini App",
        responses={200: MiniAppMeSerializer},
        tags=["Telegram"],
    )
    def get(self, request):
        employee = request.user.employee
        parts = [employee.last_name, employee.first_name, employee.middle_name]
        return Response(
            {
                "employee": {
                    "id": str(employee.id),
                    "full_name": " ".join(part for part in parts if part),
                    "employee_number": employee.employee_number,
                    "employment_status": employee.employment_status,
                    "preferred_language": employee.preferred_language,
                },
                "telegram": {
                    "status": request.user.account.status,
                    "username": request.user.account.telegram_username,
                },
            }
        )


class BotOutboxView(APIView):
    """Очередь уведомлений для бота: забрать и отчитаться.

    Бот не ходит в базу напрямую и не будет: у него есть только общий
    секрет и HTTP. Захват строк — `SELECT ... FOR UPDATE SKIP LOCKED` —
    делает backend, а бот получает готовый список.

    GET  — захватить пачку. POST — сообщить, что с ней стало.
    """

    authentication_classes = []
    permission_classes = [IsTelegramBot]

    @extend_schema(
        operation_id="telegram_bot_outbox_claim",
        summary="Забрать пачку уведомлений",
        description=(
            "Захват строк — `SELECT ... FOR UPDATE SKIP LOCKED` — делает "
            "backend: у бота нет и не будет подключения к базе. Перед "
            "выдачей возвращаются в очередь строки, зависшие после "
            "падения отправщика."
        ),
        responses={200: OutboxBatchSerializer},
        tags=["Telegram"],
    )
    def get(self, request):
        from django.conf import settings

        from humotech.notifications.outbox import claim, reclaim_stale

        # Уборка перед выдачей: строки, зависшие в RUNNING после падения
        # отправщика, иначе не ушли бы никогда.
        reclaimed = reclaim_stale()
        batch = claim(limit=settings.NOTIFICATIONS["BATCH_SIZE"])
        return Response(
            {
                "messages": [
                    {
                        "id": item.id,
                        "chat_id": item.chat_id,
                        "text": item.text,
                        "type": item.notification_type,
                        "attempts": item.attempts,
                    }
                    for item in batch
                ],
                "reclaimed": reclaimed,
            }
        )

    @extend_schema(
        operation_id="telegram_bot_outbox_report",
        summary="Отчитаться о доставке",
        description="За один запрос принимается не больше 200 результатов.",
        request=OutboxReportSerializer,
        responses={200: OutboxAcceptedSerializer},
        tags=["Telegram"],
    )
    def post(self, request):
        from humotech.notifications.outbox import mark_failed, mark_sent

        results = request.data.get("results")
        if not isinstance(results, list):
            return Response(
                {
                    "error": {
                        "code": "validation_failed",
                        "message": "Ожидался список результатов",
                        "details": {"field": "results"},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        accepted = 0
        for item in results[:200]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item.get("sent"):
                mark_sent(item["id"])
            else:
                # Текст ошибки от Telegram может содержать эхо запроса,
                # то есть само уведомление. Наружу берём только код.
                mark_failed(item["id"], error=str(item.get("error") or "unknown"))
            accepted += 1
        return Response({"accepted": accepted})
