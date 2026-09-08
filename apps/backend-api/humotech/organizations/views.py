"""REST-интерфейс настроек организации."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.core.api import validated
from humotech.core.rbac import Actor
from humotech.organizations.settings_service import (
    KNOWN_KEYS,
    OrganizationSettingsService,
)


class SettingSectionSerializer(serializers.Serializer):
    """Одна группа настроек: значения, умолчания и что каждое означает."""

    key = serializers.ChoiceField(choices=KNOWN_KEYS)
    title = serializers.CharField()
    description = serializers.CharField()
    values = serializers.DictField(
        help_text="Действующие значения организации",
    )
    defaults = serializers.DictField(
        help_text="Что действует, если организация ничего не настраивала",
    )
    help = serializers.DictField(
        child=serializers.CharField(),
        help_text="Что означает каждая настройка",
    )
    effective = serializers.DictField(
        required=False,
        help_text=(
            "Что действует на самом деле — отдельно от того, что "
            "записано. Пустой пояс CRM означает не «UTC», а «как у "
            "первого офиса», и по одним `values` это не прочесть"
        ),
    )
    updated_at = serializers.DateTimeField(
        allow_null=True,
        help_text=(
            "Редакция группы. Передаётся обратно в expected_updated_at, "
            "чтобы не затереть правку другого администратора"
        ),
    )


class SettingElsewhereSerializer(serializers.Serializer):
    """Настройка, которую здесь ищут, а живёт она не здесь."""

    name = serializers.CharField()
    owner = serializers.CharField(help_text="Где она на самом деле хранится")
    hint = serializers.CharField()


class LastChangeSerializer(serializers.Serializer):
    """Кто и когда менял настройки. `null`, если записи нет или нет права."""

    at = serializers.DateTimeField()
    action = serializers.CharField()
    actor_email = serializers.CharField(allow_null=True)


class SettingsResponseSerializer(serializers.Serializer):
    items = SettingSectionSerializer(many=True)
    elsewhere = SettingElsewhereSerializer(many=True)
    last_change = LastChangeSerializer(
        allow_null=True,
        help_text=(
            "Последнее изменение настроек из журнала действий. `null` "
            "и когда записи нет, и когда у смотрящего нет `audit.read`"
        ),
    )


class IntegrationSerializer(serializers.Serializer):
    """Подключение: что настроено и что из этого подтверждено."""

    key = serializers.CharField()
    title = serializers.CharField()
    configured = serializers.BooleanField(
        help_text="Параметры заданы развёртыванием. Не то же, что «работает»",
    )
    state = serializers.ChoiceField(
        choices=("working", "unknown", "off"),
        help_text=(
            "working — есть подтверждение работы; unknown — настроено, "
            "но проверить не удалось; off — выключено или не настроено"
        ),
    )
    note = serializers.CharField()
    confirmed_at = serializers.DateTimeField(allow_null=True)
    queued = serializers.IntegerField(allow_null=True)
    link = serializers.CharField(allow_null=True)


class IntegrationsResponseSerializer(serializers.Serializer):
    items = IntegrationSerializer(many=True)


class SettingUpdateSerializer(serializers.Serializer):
    values = serializers.DictField(
        help_text=(
            "Только известные поля группы. Неизвестное поле — ошибка, "
            "а не молчаливая запись в JSONB."
        ),
    )
    expected_updated_at = serializers.DateTimeField(
        required=False, allow_null=True,
        help_text="Редакция, которую видел правящий. Не совпала — 409",
    )
    check_expected = serializers.BooleanField(
        required=False, default=False,
        help_text=(
            "true — сверять expected_updated_at. Отдельный признак нужен "
            "потому, что у ненастроенной группы редакции нет вовсе, и "
            "null — законное значение, а не «не передали»"
        ),
    )


@extend_schema(tags=["Настройки"])
class OrganizationSettingsView(APIView):
    """Все настройки организации. Требует `settings.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="settings_list",
        summary="Настройки организации",
        description=(
            "Значения, умолчания и пояснение к каждой настройке. Правила "
            "отсутствий действуют на решения, принимаемые после изменения: "
            "уже подтверждённые отсутствия не пересматриваются."
        ),
        responses={200: SettingsResponseSerializer},
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        return Response(OrganizationSettingsService().all(actor))


@extend_schema(tags=["Настройки"])
class OrganizationSettingDetailView(APIView):
    """Одна группа настроек: прочитать и записать."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Одна группа настроек",
        responses={200: SettingSectionSerializer},
    )
    def get(self, request, key: str):
        actor = Actor.from_user(request.user)
        return Response(OrganizationSettingsService().get(actor, key))

    @extend_schema(
        summary="Изменить настройки группы",
        description=(
            "Меняются только переданные поля, остальные сохраняются. "
            "Неизвестное поле отвергается: настройка, которая выглядит "
            "записанной и не работает, хуже явной ошибки."
        ),
        request=SettingUpdateSerializer,
        responses={200: SettingSectionSerializer},
        examples=[
            OpenApiExample(
                "Справка только с четвёртого дня",
                value={"values": {"document_required": True,
                                  "document_required_from_day": 4}},
                request_only=True,
            ),
            OpenApiExample(
                "Пояс организации",
                value={"values": {"default_timezone": "Asia/Dushanbe"}},
                request_only=True,
            ),
        ],
    )
    def patch(self, request, key: str):
        actor = Actor.from_user(request.user)
        payload = validated(SettingUpdateSerializer, request.data)
        return Response(
            OrganizationSettingsService().update(
                actor,
                key,
                payload["values"],
                expected_updated_at=payload.get("expected_updated_at"),
                check_expected=payload.get("check_expected", False),
            )
        )


@extend_schema(tags=["Настройки"])
class IntegrationsView(APIView):
    """Состояние подключений. Только чтение, требует `settings.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="settings_integrations",
        summary="Подключения: что настроено и что подтверждено",
        description=(
            "Ни одного исходящего запроса при просмотре: «работает» "
            "выводится из следов, которые система оставила сама — "
            "успешной попытки отправки, — а не из опроса внешнего "
            "сервиса. Заполненная переменная окружения означает "
            "«настроено», но не «доступно». "
            "Токенов, секретов и строк подключения в ответе нет — ни "
            "целиком, ни префиксами."
        ),
        responses={200: IntegrationsResponseSerializer},
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        return Response(OrganizationSettingsService().integrations(actor))


__all__ = [
    "IntegrationsView",
    "OrganizationSettingDetailView",
    "OrganizationSettingsView",
]
