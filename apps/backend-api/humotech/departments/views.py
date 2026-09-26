"""REST-интерфейс отделов и должностей."""

from __future__ import annotations

import uuid

from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.core.api import ServiceViewSet, validated
from humotech.core.errors import ValidationFailed
from humotech.departments.services import DepartmentService, PositionService


class DepartmentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    office_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(
        source="office.name", allow_null=True, default=None
    )
    parent_department_id = serializers.UUIDField(allow_null=True)
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    head_employee_id = serializers.UUIDField(allow_null=True)
    head_employee_name = serializers.SerializerMethodField()
    # Сколько человек числится в отделе сегодня. Нужно не для красоты:
    # архивировать отдел с людьми нельзя, и число объясняет отказ до
    # того, как кадровик его получит.
    staff = serializers.IntegerField(required=False)
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_head_employee_name(self, department) -> str | None:
        head = getattr(department, "head_employee", None)
        if head is None:
            return None
        parts = [head.last_name, head.first_name, head.middle_name]
        return " ".join(part for part in parts if part) or None


class DepartmentCreateSerializer(serializers.Serializer):
    # Офис необязателен: обычный отдел общий для компании. Указывают его
    # только для подразделения, которое существует в одном месте.
    office_id = serializers.UUIDField(required=False, allow_null=True)
    # Код не обязателен: в интерфейсе его не спрашивают, и сервер
    # придумывает его сам. Поле остаётся для переноса данных, где код
    # задан заранее.
    code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    head_employee_id = serializers.UUIDField(required=False, allow_null=True)
    parent_department_id = serializers.UUIDField(required=False, allow_null=True)


class DepartmentUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    head_employee_id = serializers.UUIDField(required=False, allow_null=True)
    clear_head = serializers.BooleanField(
        required=False, help_text="true — снять руководителя отдела"
    )


class PositionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    #: Сколько человек занимают должность сегодня.
    staff = serializers.IntegerField(required=False)
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class PositionCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)


class PositionUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)


def _uuid(request, name: str) -> uuid.UUID | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError) as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть UUID", details={"field": name}
        ) from exc


class DepartmentViewSet(ServiceViewSet):
    """Отделы офиса. Удаления нет: на отдел ссылаются закрытые назначения,
    и стереть его значило бы потерять, кем человек работал в прошлом году."""

    service_class = DepartmentService
    read_serializer_class = DepartmentSerializer

    def list(self, request):
        return self.page_response(
            self.service.list(
                self.actor,
                **self.list_params(),
                office_id=_uuid(request, "office_id"),
                region_id=_uuid(request, "region_id"),
            )
        )

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    def create(self, request):
        payload = validated(DepartmentCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True
        )

    def partial_update(self, request, pk=None):
        payload = validated(DepartmentUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))


    def destroy(self, request, pk=None):
        """Убрать совсем. Сервер откажет, если на запись уже ссылались."""
        self.service.delete(self.actor, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(
            self.service.set_status(self.actor, pk, status="INACTIVE")
        )

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self.item_response(
            self.service.set_status(self.actor, pk, status="ACTIVE")
        )


class PositionViewSet(ServiceViewSet):
    """Должности организации. Области видимости у них нет: должность
    не принадлежит офису, «Инженер» одинаков для всех."""

    service_class = PositionService
    read_serializer_class = PositionSerializer

    def list(self, request):
        return self.page_response(self.service.list(self.actor, **self.list_params()))

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    def create(self, request):
        payload = validated(PositionCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True
        )

    def partial_update(self, request, pk=None):
        payload = validated(PositionUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))


    def destroy(self, request, pk=None):
        """Убрать совсем. Сервер откажет, если на запись уже ссылались."""
        self.service.delete(self.actor, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(
            self.service.set_status(self.actor, pk, status="INACTIVE")
        )

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self.item_response(
            self.service.set_status(self.actor, pk, status="ACTIVE")
        )
