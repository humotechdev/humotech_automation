"""REST-интерфейс экранов показа QR.

Два разных адресных пространства, и разделены они намеренно:

  * `/api/v1/qr-display/` — то, чем пользуется сам экран. На этом префиксе
    работает своя зона CORS со своим списком origin'ов;
  * `/api/v1/qr/devices` — управление экранами из CRM. Сюда экран не ходит
    и ходить не должен, поэтому и адрес другой: окажись оно на префиксе
    экранов, добавление адреса экрана в CORS попутно открыло бы браузеру
    из офиса управление устройствами.

Ограничение частоты стоит на обоих, но по разным причинам. У сопряжения —
против перебора одноразового кода. У выдачи кодов — против экрана, который
после обрыва связи начинает опрашивать сервер без пауз.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from humotech.core.api import validated
from humotech.core.errors import PermissionDenied
from humotech.core.rbac import Actor
from humotech.qr_codes.serializers import (
    DeviceCreateSerializer,
    DeviceSerializer,
    IssuedDeviceSerializer,
    IssuedQrSerializer,
    PairedDeviceSerializer,
    PairSerializer,
)
from humotech.qr_codes.services import QrDisplayService

DISPLAY_CREDENTIAL_KEYWORD = "Bearer"


def _credential(request) -> str | None:
    """Credential экрана из заголовка Authorization."""
    header = request.headers.get("Authorization", "")
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != DISPLAY_CREDENTIAL_KEYWORD.lower():
        return None
    return parts[1]


class QrDisplayPairView(APIView):
    """Обмен одноразового кода сопряжения на собственный credential экрана.

    Открыт без аутентификации намеренно: право выполнить операцию даёт сам
    код, а предъявить что-то ещё экрану нечего — он для того сюда и пришёл,
    чтобы получить своё первое удостоверение.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "qr_display_pair"

    def get_throttles(self):
        return [ScopedRateThrottle()]

    def post(self, request):
        data = validated(PairSerializer, request.data)
        paired = QrDisplayService().pair(data["pairing_code"])
        return Response(
            PairedDeviceSerializer(paired).data, status=status.HTTP_201_CREATED
        )


class QrDisplayCodeView(APIView):
    """Очередной код для показа.

    Ни офис, ни точка в запросе не участвуют: и то и другое сервер берёт
    из устройства. Экран не может попросить код чужого офиса, потому что
    попросить его нечем.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "qr_display_code"

    def get_throttles(self):
        return [ScopedRateThrottle()]

    def get(self, request):
        credential = _credential(request)
        service = QrDisplayService()
        device = (
            service.authenticate_device(credential) if credential else None
        )
        if device is None:
            # Одна причина на все случаи: истёк срок, отозван экран,
            # подделан credential. Разница ответов помогала бы подбирать.
            raise PermissionDenied(
                "Экран не авторизован", details={"reason": "display_not_authorized"}
            )

        issued = service.issue_qr(device)
        return Response(IssuedQrSerializer(issued).data)


class QrDeviceListView(APIView):
    """Список экранов и заведение нового — из CRM."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = Actor.from_user(request.user)
        devices = QrDisplayService().list_devices(
            actor, qr_point_id=request.query_params.get("qr_point_id") or None
        )
        return Response(DeviceSerializer(devices, many=True).data)

    def post(self, request):
        data = validated(DeviceCreateSerializer, request.data)
        actor = Actor.from_user(request.user)
        issued = QrDisplayService().create_device(
            actor, qr_point_id=data["qr_point_id"], name=data["name"]
        )
        # Код сопряжения существует только в этом ответе. Восстановить его
        # потом нельзя даже суперпользователю: в базе лежит хеш.
        return Response(
            IssuedDeviceSerializer(issued).data, status=status.HTTP_201_CREATED
        )


class QrDeviceActionView(APIView):
    """Отзыв экрана и перевыпуск кода сопряжения."""

    permission_classes = [IsAuthenticated]

    def post(self, request, device_id, action):
        actor = Actor.from_user(request.user)
        service = QrDisplayService()
        if action == "revoke":
            device = service.revoke_device(actor, device_id)
            return Response(DeviceSerializer(device).data)
        issued = service.reissue_pairing(actor, device_id)
        return Response(IssuedDeviceSerializer(issued).data)


__all__ = [
    "QrDeviceActionView",
    "QrDeviceListView",
    "QrDisplayCodeView",
    "QrDisplayPairView",
]
