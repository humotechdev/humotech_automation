"""Сериализаторы экранов показа QR.

Отдельно стоит посмотреть, чего здесь нет. В ответе экрану — ни одного
идентификатора сотрудника, ни списка людей, ни статистики офиса: экран
висит в коридоре, и всё, что он показывает, видят посторонние. Из офиса
он знает только название — чтобы человек убедился, что подошёл к своей
двери, а не к соседней.
"""

from __future__ import annotations

from rest_framework import serializers


class PairSerializer(serializers.Serializer):
    """Одноразовый код сопряжения."""

    pairing_code = serializers.CharField(max_length=200, trim_whitespace=True)


class DeviceCreateSerializer(serializers.Serializer):
    qr_point_id = serializers.UUIDField()
    name = serializers.CharField(max_length=255)


class DeviceSerializer(serializers.Serializer):
    """Экран глазами HR. Ни секретов, ни их хешей."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    qr_point_id = serializers.UUIDField(read_only=True)
    qr_point_name = serializers.CharField(source="qr_point.name", read_only=True)
    office_name = serializers.CharField(source="qr_point.office.name", read_only=True)
    paired_at = serializers.DateTimeField(read_only=True)
    last_seen_at = serializers.DateTimeField(read_only=True)
    revoked_at = serializers.DateTimeField(read_only=True)
    credential_expires_at = serializers.DateTimeField(read_only=True)


class IssuedDeviceSerializer(serializers.Serializer):
    """Ответ на заведение экрана. Код сопряжения — только здесь и один раз."""

    device = DeviceSerializer(read_only=True)
    pairing_code = serializers.CharField(read_only=True)


class PairedDeviceSerializer(serializers.Serializer):
    """Ответ на сопряжение. `credential` больше нигде не восстановим."""

    credential = serializers.CharField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)
    office_name = serializers.CharField(
        source="device.qr_point.office.name", read_only=True
    )
    point_name = serializers.CharField(source="device.qr_point.name", read_only=True)
    direction_mode = serializers.CharField(
        source="device.qr_point.direction_mode", read_only=True
    )


class IssuedQrSerializer(serializers.Serializer):
    """Код для показа плюс всё, что экрану нужно, чтобы обновиться вовремя."""

    token = serializers.CharField(read_only=True)
    issued_at = serializers.DateTimeField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)
    office_name = serializers.CharField(read_only=True)
    point_name = serializers.CharField(read_only=True)
    direction_mode = serializers.CharField(read_only=True)


__all__ = [
    "DeviceCreateSerializer",
    "DeviceSerializer",
    "IssuedDeviceSerializer",
    "IssuedQrSerializer",
    "PairSerializer",
    "PairedDeviceSerializer",
]


# --- точки отметки ----------------------------------------------------------


class QrPointSerializer(serializers.Serializer):
    """Карточка точки.

    Поля `static_token_hash` здесь нет намеренно, и это не забывчивость:
    сериализатор, который его не знает, не сможет его отдать даже по
    ошибке. Сам секрет живёт ровно в одном ответе — на выпуск.
    """

    id = serializers.UUIDField()
    office_id = serializers.UUIDField()
    office_name = serializers.CharField(source="office.name")
    region_id = serializers.UUIDField(source="office.region_id", allow_null=True)

    code = serializers.CharField()
    name = serializers.CharField()
    direction_mode = serializers.CharField()
    qr_mode = serializers.CharField()
    rotation_seconds = serializers.IntegerField(allow_null=True)
    token_version = serializers.IntegerField()
    require_geolocation = serializers.BooleanField()
    require_office_network = serializers.BooleanField()
    allowed_location_accuracy_m = serializers.IntegerField(allow_null=True)
    is_active = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class IssuedQrPointSerializer(serializers.Serializer):
    """Точка вместе с секретом — единственное место, где секрет виден.

    `static_token` возвращается один раз. Записать его должен тот, кто
    печатает наклейку; повторно узнать его нельзя ни через API, ни через
    базу — там только хеш.
    """

    point = QrPointSerializer()
    static_token = serializers.CharField(allow_null=True)


class QrPointCreateSerializer(serializers.Serializer):
    office_id = serializers.UUIDField()
    code = serializers.CharField(max_length=100)
    name = serializers.CharField(max_length=255)
    direction_mode = serializers.ChoiceField(
        choices=["ENTRY", "EXIT", "BOTH"], default="BOTH"
    )
    qr_mode = serializers.ChoiceField(choices=["STATIC", "ROTATING"],
                                      default="ROTATING")
    rotation_seconds = serializers.IntegerField(required=False, allow_null=True)
    require_geolocation = serializers.BooleanField(default=False)
    require_office_network = serializers.BooleanField(default=False)
    allowed_location_accuracy_m = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )


class QrPointUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    direction_mode = serializers.ChoiceField(
        choices=["ENTRY", "EXIT", "BOTH"], required=False
    )
    rotation_seconds = serializers.IntegerField(required=False)
    require_geolocation = serializers.BooleanField(required=False)
    require_office_network = serializers.BooleanField(required=False)
    allowed_location_accuracy_m = serializers.IntegerField(
        required=False, min_value=1
    )
