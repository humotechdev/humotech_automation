"""REST-интерфейс учётных записей, ролей и областей видимости.

Отдельно от `accounts/views.py`: там вход и «кто я», здесь управление
доступом других людей. Разрешения тоже разные — `users.manage` и
`roles.manage` против «быть авторизованным».
"""

from __future__ import annotations

import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.accounts.rbac_service import RoleAdminService, UserAdminService
from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import USER_STATUSES
from humotech.core.rbac import Actor


class CrmUserSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.CharField()
    status = serializers.ChoiceField(choices=USER_STATUSES)
    mfa_enabled = serializers.BooleanField()
    employee_id = serializers.UUIDField(allow_null=True)
    last_login = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class CrmUserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    employee_id = serializers.UUIDField(required=False, allow_null=True)


class RoleSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    is_system = serializers.BooleanField()
    permissions = serializers.ListField(child=serializers.CharField())
    grantable = serializers.BooleanField(
        help_text="Может ли ЭТОТ пользователь выдать эту роль",
    )
    missing_permissions = serializers.ListField(
        child=serializers.CharField(),
        help_text="Чего не хватает выдающему, если роль недоступна",
    )


class RoleListSerializer(serializers.Serializer):
    items = RoleSerializer(many=True)


class GrantSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    role_id = serializers.UUIDField()
    role_code = serializers.CharField(source="role.code")
    role_name = serializers.CharField(source="role.name")
    region_id = serializers.UUIDField(allow_null=True)
    region_name = serializers.CharField(
        source="region.name", allow_null=True, default=None
    )
    office_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(
        source="office.name", allow_null=True, default=None
    )
    valid_from = serializers.DateTimeField()
    valid_to = serializers.DateTimeField(allow_null=True)


class GrantListSerializer(serializers.Serializer):
    items = GrantSerializer(many=True)


class GrantCreateSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    role_id = serializers.UUIDField()
    region_id = serializers.UUIDField(required=False, allow_null=True)
    office_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Без региона и офиса — доступ на всю организацию",
    )
    valid_from = serializers.DateTimeField(required=False, allow_null=True)
    valid_to = serializers.DateTimeField(required=False, allow_null=True)


class GrantValiditySerializer(serializers.Serializer):
    valid_to = serializers.DateTimeField(
        allow_null=True, help_text="null — бессрочно"
    )


@extend_schema(tags=["Пользователи"])
class CrmUserViewSet(ServiceViewSet):
    """Учётные записи CRM. Требует `users.manage`.

    Пароль здесь не задаётся и не меняется. Новая запись заводится без
    пригодного пароля и в статусе INACTIVE: вход открывается, когда
    человек установит пароль сам.
    """

    service_class = UserAdminService
    read_serializer_class = CrmUserSerializer

    @extend_schema(
        summary="Список учётных записей",
        parameters=[
            OpenApiParameter("search", str, description="Подстрока адреса"),
            OpenApiParameter("status", str, enum=list(USER_STATUSES)),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
    )
    def list(self, request):
        return self.page_response(self.service.list(self.actor, **self.list_params()))

    @extend_schema(summary="Одна учётная запись")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    @extend_schema(
        summary="Завести учётную запись",
        description=(
            "Создаётся без пароля и в статусе INACTIVE. Пароль "
            "устанавливает сам человек: запись, чей пароль знает кто-то "
            "ещё, не отвечает на вопрос «кто это сделал»."
        ),
        request=CrmUserCreateSerializer,
        responses={201: CrmUserSerializer},
    )
    def create(self, request):
        payload = validated(CrmUserCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True
        )

    @extend_schema(summary="Включить учётную запись", responses={200: CrmUserSerializer})
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self.item_response(
            self.service.set_status(self.actor, pk, status="ACTIVE")
        )

    @extend_schema(
        summary="Отключить учётную запись",
        description=(
            "Отказ, если это последний действующий суперадминистратор "
            "организации: остаться без него значит потерять управление."
        ),
        responses={200: CrmUserSerializer},
    )
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(
            self.service.set_status(self.actor, pk, status="INACTIVE")
        )


@extend_schema(tags=["Роли"])
class RoleListView(APIView):
    """Роли с их разрешениями. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Роли и их разрешения",
        description=(
            "Рядом с каждой ролью — может ли ЭТОТ пользователь её выдать "
            "и чего ему для этого не хватает. Считает сервер: кнопка, "
            "скрытая на клиенте, защитой не является."
        ),
        responses={200: RoleListSerializer},
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        return Response({"items": RoleAdminService().roles(actor)})


@extend_schema(tags=["Роли"])
class UserGrantsView(APIView):
    """Назначения одного пользователя. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Назначения пользователя",
        parameters=[
            OpenApiParameter(
                "history", OpenApiTypes.BOOL,
                description=(
                    "true — вся история, включая отозванные. Отзыв не "
                    "удаляет строку, поэтому история есть всегда."
                ),
            ),
        ],
        responses={200: GrantListSerializer},
    )
    def get(self, request, user_id: uuid.UUID):
        actor = Actor.from_user(request.user)
        history = str(request.query_params.get("history", "")).lower() in (
            "1", "true", "yes",
        )
        rows = RoleAdminService().assignments(
            actor, user_id, include_expired=history
        )
        return Response({"items": GrantSerializer(rows, many=True).data})


@extend_schema(tags=["Роли"])
class GrantCreateView(APIView):
    """Выдача роли. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Выдать роль",
        description=(
            "Проверок две: набор прав роли обязан входить в набор "
            "выдающего, И территория назначения обязана лежать внутри "
            "его области. Одной первой мало — список прав собирается по "
            "всем областям сразу и территорию не ограничивает."
        ),
        request=GrantCreateSerializer,
        responses={201: GrantSerializer},
        examples=[
            OpenApiExample(
                "Кадровик одного региона",
                value={"user_id": "…", "role_id": "…", "region_id": "…"},
                request_only=True,
            ),
            OpenApiExample(
                "Временный доступ до конца квартала",
                value={"user_id": "…", "role_id": "…", "office_id": "…",
                       "valid_to": "2026-06-30T18:00:00+05:00"},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        actor = Actor.from_user(request.user)
        payload = validated(GrantCreateSerializer, request.data)
        grant = RoleAdminService().assign(actor, **payload)
        return Response(GrantSerializer(grant).data, status=201)


@extend_schema(tags=["Роли"])
class GrantDetailView(APIView):
    """Отзыв и продление одного назначения. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Изменить срок назначения",
        request=GrantValiditySerializer,
        responses={200: GrantSerializer},
    )
    def patch(self, request, grant_id: uuid.UUID):
        actor = Actor.from_user(request.user)
        payload = validated(GrantValiditySerializer, request.data)
        grant = RoleAdminService().set_validity(
            actor, grant_id, valid_to=payload["valid_to"]
        )
        return Response(GrantSerializer(grant).data)

    @extend_schema(
        summary="Отозвать роль",
        description=(
            "Строка остаётся, закрывается срок: удаление стёрло бы "
            "ответ на вопрос «кто и когда дал человеку этот доступ». "
            "Отказ, если это роль последнего суперадминистратора."
        ),
        responses={200: GrantSerializer},
    )
    def delete(self, request, grant_id: uuid.UUID):
        actor = Actor.from_user(request.user)
        grant = RoleAdminService().revoke(actor, grant_id)
        return Response(GrantSerializer(grant).data)


__all__ = [
    "CrmUserViewSet",
    "GrantCreateView",
    "GrantDetailView",
    "RoleListView",
    "UserGrantsView",
]
