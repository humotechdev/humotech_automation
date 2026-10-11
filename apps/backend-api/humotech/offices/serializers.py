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
    pending_location_by_name = serializers.SerializerMethodField(
        help_text="Кто предложил новое место; пусто — предложения нет",
    )

    class Meta:
        model = Office
        fields = (
            "id", "organization_id", "region_id", "region_code", "region_name",
            "code", "name", "address", "timezone",
            "latitude", "longitude", "geofence_radius_m",
            "geofence_kind", "geofence_polygon",
            # Новое место, ждущее подтверждения. Действует — то, что выше.
            "pending_location", "pending_location_at", "pending_location_by_name",
            "status", "opened_at", "closed_at", "created_at", "updated_at",
        )
        read_only_fields = fields

    def get_pending_location_by_name(self, office) -> str | None:
        if not office.pending_location or office.pending_location_by_user_id is None:
            return None
        user = office.pending_location_by_user
        return (getattr(user, "full_name", None) or user.email) if user else None


class OfficeCreateSerializer(serializers.Serializer):
    """Регион и местоположение.

    Название пустым можно оставить — офис назовётся по региону. Местоположение
    обязательно: точка с радиусом или контур. Без него на офисе не работала бы
    ни одна проверка присутствия, поэтому сервис откажет в создании. Здесь
    поля необязательны по виду: какой набор нужен — круг или контур, —
    решает сервис одним правилом с правкой.
    """

    region_id = serializers.UUIDField()
    # Код не обязателен: в интерфейсе его не спрашивают, сервер
    # придумывает сам. Поле осталось для переноса данных.
    code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    # Пояс не обязателен: в стране он один и берётся у организации.
    timezone = serializers.CharField(
        max_length=100, required=False, allow_blank=True, allow_null=True
    )
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True
    )
    geofence_radius_m = serializers.IntegerField(required=False, allow_null=True)
    geofence_kind = serializers.ChoiceField(
        choices=("CIRCLE", "POLYGON"), required=False
    )
    # Углы участка: `[[широта, долгота], …]`. Здесь проверяется только
    # вид; самопересечения, размер и число углов — в сервисе, одним
    # правилом для создания и правки.
    geofence_polygon = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(), min_length=2, max_length=2
        ),
        required=False, allow_null=True, max_length=200,
    )
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
    geofence_kind = serializers.ChoiceField(
        choices=("CIRCLE", "POLYGON"), required=False
    )
    # Углы участка: `[[широта, долгота], …]`. Здесь проверяется только
    # вид; самопересечения, размер и число углов — в сервисе, одним
    # правилом для создания и правки.
    geofence_polygon = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(), min_length=2, max_length=2
        ),
        required=False, allow_null=True, max_length=200,
    )
    opened_at = serializers.DateField(required=False, allow_null=True)
    closed_at = serializers.DateField(required=False, allow_null=True)


class OfficeCloseSerializer(serializers.Serializer):
    closed_at = serializers.DateField(required=False, allow_null=True)
