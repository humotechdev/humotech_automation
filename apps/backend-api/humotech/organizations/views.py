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


class SettingElsewhereSerializer(serializers.Serializer):
    """Настройка, которую здесь ищут, а живёт она не здесь."""

    name = serializers.CharField()
    owner = serializers.CharField(help_text="Где она на самом деле хранится")
    hint = serializers.CharField()


class SettingsResponseSerializer(serializers.Serializer):
    items = SettingSectionSerializer(many=True)
    elsewhere = SettingElsewhereSerializer(many=True)


class SettingUpdateSerializer(serializers.Serializer):
    values = serializers.DictField(
        help_text=(
            "Только известные поля группы. Неизвестное поле — ошибка, "
            "а не молчаливая запись в JSONB."
        ),
    )


@extend_schema(tags=["Настройки"])
class OrganizationSettingsView(APIView):
    """Все настройки организации. Требует `settings.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
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
            OrganizationSettingsService().update(actor, key, payload["values"])
        )


__all__ = ["OrganizationSettingDetailView", "OrganizationSettingsView"]
