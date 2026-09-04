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
