"""REST-интерфейс справочника причин отсутствия.

Удаления среди действий нет намеренно: вид отсутствия живёт в истории
заявок, и единственный способ убрать его из обихода — выключить.
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.absences.types_service import AbsenceTypeService
from humotech.core.api import ServiceViewSet, validated


class AbsenceTypeCatalogSerializer(serializers.Serializer):
    """Вид отсутствия глазами кадровика: правила и место в истории.

    Имя не `AbsenceTypeSerializer`: так называется краткая карточка вида
    в личном кабинете сотрудника, и два разных набора полей под одним
    именем дают неверную схему OpenAPI, а по ней — неверного клиента.
    """

    id = serializers.UUIDField()
    # Код технический: он нужен выгрузкам и уникальному ключу, в
    # интерфейсе его не показывают.
    code = serializers.CharField()
    name = serializers.CharField()
    is_paid = serializers.BooleanField()
    requires_approval = serializers.BooleanField()
    requires_document = serializers.BooleanField()
    document_required_after_days = serializers.IntegerField(allow_null=True)
    deducts_leave_balance = serializers.BooleanField()
    is_active = serializers.BooleanField()
    #: Сколько раз вид уже встречается в заявках и подтверждённых
    #: периодах. Ненулевое число означает, что удалить его нельзя.
    used = serializers.IntegerField(required=False)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class AbsenceTypeCatalogCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    is_paid = serializers.BooleanField(required=False)
    requires_approval = serializers.BooleanField(required=False)
    requires_document = serializers.BooleanField(required=False)
    document_required_after_days = serializers.IntegerField(
        required=False, allow_null=True
    )
    deducts_leave_balance = serializers.BooleanField(required=False)


class AbsenceTypeCatalogUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    is_paid = serializers.BooleanField(required=False)
    requires_approval = serializers.BooleanField(required=False)
    requires_document = serializers.BooleanField(required=False)
    document_required_after_days = serializers.IntegerField(
        required=False, allow_null=True
    )
    deducts_leave_balance = serializers.BooleanField(required=False)


class AbsenceTypeViewSet(ServiceViewSet):
    """Виды отсутствия организации."""

    service_class = AbsenceTypeService
    read_serializer_class = AbsenceTypeCatalogSerializer

    def list(self, request):
        return self.page_response(self.service.list(self.actor, **self.list_params()))

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    def create(self, request):
        payload = validated(AbsenceTypeCatalogCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True
        )

    def partial_update(self, request, pk=None):
        payload = validated(AbsenceTypeCatalogUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))


    def destroy(self, request, pk=None):
        """Убрать совсем. Сервер откажет, если на запись уже ссылались."""
        self.service.delete(self.actor, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(
            self.service.set_active(self.actor, pk, active=False)
        )

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self.item_response(self.service.set_active(self.actor, pk, active=True))
