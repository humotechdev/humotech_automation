"""Представление регионов в API."""

from __future__ import annotations

from rest_framework import serializers

from humotech.regions.models import Region


class RegionSerializer(serializers.ModelSerializer):
    """Строка списка и карточка.

    `offices_count` приходит аннотацией списка, в карточке его нет —
    поэтому поле объявлено необязательным, а не «иногда пустым».
    """

    offices_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Region
        fields = (
            "id", "organization_id", "code", "name", "timezone", "status",
            "offices_count", "created_at", "updated_at",
        )
        read_only_fields = fields


class RegionCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    name = serializers.CharField(max_length=255)
    timezone = serializers.CharField(
        max_length=100, required=False, allow_null=True
    )


class RegionUpdateSerializer(serializers.Serializer):
    """Меняются только переданные поля.

    `timezone: null` означает «наследовать пояс организации», поэтому
    отсутствие ключа и явный null — разные вещи. Сериализатор их различает:
    в `validated_data` попадает только то, что действительно прислали.
    """

    code = serializers.CharField(max_length=50, required=False)
    name = serializers.CharField(max_length=255, required=False)
    timezone = serializers.CharField(
        max_length=100, required=False, allow_null=True
    )
