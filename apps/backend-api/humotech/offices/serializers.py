"""Представление офисов в API."""

from __future__ import annotations

from rest_framework import serializers

from humotech.offices.models import Office


class OfficeSerializer(serializers.ModelSerializer):
    """Регион разворачивается кодом и названием.

    Без них карточка офиса нечитаема, а отдельный запрос за регионом на
    каждую строку — это N+1. Список подтягивает регион через `select_related`,
    поэтому обращение к `office.region` дополнительного запроса не делает.
    """

    region_code = serializers.CharField(source="region.code", read_only=True)
    region_name = serializers.CharField(source="region.name", read_only=True)

    class Meta:
        model = Office
        fields = (
            "id", "organization_id", "region_id", "region_code", "region_name",
            "code", "name", "address", "timezone",
            "latitude", "longitude", "geofence_radius_m",
            "status", "opened_at", "closed_at", "created_at", "updated_at",
        )
        read_only_fields = fields


class OfficeCreateSerializer(serializers.Serializer):
    region_id = serializers.UUIDField()
    code = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=255)
    address = serializers.CharField()
    timezone = serializers.CharField(max_length=100)
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True
    )
    geofence_radius_m = serializers.IntegerField(required=False, allow_null=True)
    opened_at = serializers.DateField(required=False, allow_null=True)


class OfficeUpdateSerializer(serializers.Serializer):
    region_id = serializers.UUIDField(required=False)
    code = serializers.CharField(max_length=50, required=False)
    name = serializers.CharField(max_length=255, required=False)
    address = serializers.CharField(required=False)
    timezone = serializers.CharField(max_length=100, required=False)
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True
    )
    geofence_radius_m = serializers.IntegerField(required=False, allow_null=True)
    opened_at = serializers.DateField(required=False, allow_null=True)
    closed_at = serializers.DateField(required=False, allow_null=True)


class OfficeCloseSerializer(serializers.Serializer):
    closed_at = serializers.DateField(required=False, allow_null=True)
