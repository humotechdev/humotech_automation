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

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from rest_framework.decorators import action

from humotech.core.api import ServiceViewSet, validated
from humotech.core.errors import PermissionDenied
from humotech.core.rbac import Actor
from humotech.qr_codes.points import QrPointService
from humotech.qr_codes.serializers import (
    DeviceCreateSerializer,
    DeviceSerializer,
    IssuedDeviceSerializer,
    IssuedQrPointSerializer,
    IssuedQrSerializer,
    PairedDeviceSerializer,
    PairSerializer,
    QrPointCreateSerializer,
    QrPointSerializer,
    QrPointUpdateSerializer,
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

    @extend_schema(
        operation_id="qr_display_pair",
        summary="Сопряжение экрана",
        description=(
            "Одноразовый код обменивается на постоянный credential "
            "экрана. Credential существует только в этом ответе: "
            "в базе лежит хеш, и восстановить его потом нельзя.\n\n"
            "Частота обращений ограничена — против перебора кода."
        ),
        request=PairSerializer,
        responses={201: PairedDeviceSerializer},
        tags=["Экраны QR"],
    )
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

    @extend_schema(
        operation_id="qr_display_code",
        summary="Очередной код для показа",
        description=(
            "Экран предъявляет свой credential в заголовке "
            "`Authorization: Bearer`. Ни офис, ни точка в запросе не "
            "участвуют: и то и другое сервер берёт из устройства — "
            "попросить код чужого офиса экрану попросту нечем."
        ),
        request=None,
        responses={
            200: IssuedQrSerializer,
            403: OpenApiResponse(
                description=(
                    "Одна причина на все случаи: истёк срок, отозван "
                    "экран, подделан credential"
                ),
            ),
        },
        tags=["Экраны QR"],
    )
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


@extend_schema(tags=["Экраны QR"])
class QrDeviceListView(APIView):
    """Список экранов и заведение нового — из CRM."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="qr_devices_list",
        summary="Экраны показа QR",
        parameters=[
            OpenApiParameter("qr_point_id", OpenApiTypes.UUID),
        ],
        responses={200: DeviceSerializer(many=True)},
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        devices = QrDisplayService().list_devices(
            actor, qr_point_id=request.query_params.get("qr_point_id") or None
        )
        return Response(DeviceSerializer(devices, many=True).data)

    @extend_schema(
        operation_id="qr_devices_create",
        summary="Завести экран",
        description=(
            "Код сопряжения существует только в этом ответе. "
            "Восстановить его потом нельзя даже суперпользователю: "
            "в базе лежит хеш."
        ),
        request=DeviceCreateSerializer,
        responses={201: IssuedDeviceSerializer},
    )
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


@extend_schema(tags=["Экраны QR"])
class QrDeviceActionView(APIView):
    """Отзыв экрана и перевыпуск кода сопряжения."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="qr_device_action",
        summary="Отозвать экран или перевыпустить код сопряжения",
        parameters=[
            OpenApiParameter(
                "device_id", OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
            ),
            OpenApiParameter(
                "action", str, location=OpenApiParameter.PATH,
                enum=["revoke", "reissue"],
            ),
        ],
        request=None,
        responses={
            200: OpenApiResponse(
                response=IssuedDeviceSerializer,
                description=(
                    "При `reissue` — новый код сопряжения, видимый "
                    "только здесь. При `revoke` — карточка экрана без кода"
                ),
            ),
        },
    )
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


class QrPointViewSet(ServiceViewSet):
    """Справочник точек отметки.

    Секрет статической точки возвращается ровно в двух ответах — на
    создание и на перевыпуск — и больше нигде. Сериализатор списка и
    карточки поля с секретом не знает вовсе.
    """

    service_class = QrPointService
    read_serializer_class = QrPointSerializer

    def list(self, request):
        params = self.list_params()
        params.pop("status", None)
        active = request.query_params.get("is_active")
        return self.page_response(
            self.service.list(
                self.actor,
                **params,
                office_id=_uuid_or_none(request, "office_id"),
                region_id=_uuid_or_none(request, "region_id"),
                is_active=None if active is None else active.lower() == "true",
            )
        )

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    def create(self, request):
        payload = validated(QrPointCreateSerializer, request.data)
        issued = self.service.create(self.actor, **payload)
        return Response(
            IssuedQrPointSerializer(issued).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, pk=None):
        payload = validated(QrPointUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))

    @extend_schema(
        summary="Удалить точку отметки",
        description=(
            "Только ту, по которой никто не отмечался. Точка, попавшая "
            "хоть в одну отметку, — часть истории: её выключают, а не "
            "стирают."
        ),
        responses={204: None},
    )
    def destroy(self, request, pk=None):
        self.service.delete(self.actor, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self.item_response(
            self.service.set_active(self.actor, pk, active=True)
        )

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(
            self.service.set_active(self.actor, pk, active=False)
        )

    @action(detail=True, methods=["post"], url_path="reissue-token")
    def reissue_token(self, request, pk=None):
        """Новый секрет наклейки. Прежний перестаёт работать сразу."""
        issued = self.service.reissue_static_token(self.actor, pk)
        return Response(IssuedQrPointSerializer(issued).data)


def _uuid_or_none(request, name: str):
    import uuid as _uuid

    from humotech.core.errors import ValidationFailed

    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return _uuid.UUID(raw)
    except (ValueError, TypeError) as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть UUID", details={"field": name}
        ) from exc
