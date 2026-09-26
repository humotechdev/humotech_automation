"""REST-интерфейс справочника офисов."""

from __future__ import annotations

from rest_framework.decorators import action

from humotech.core.api import ServiceViewSet, query_uuid, validated
from humotech.offices.serializers import (
    OfficeCloseSerializer,
    OfficeCreateSerializer,
    OfficeSerializer,
    OfficeUpdateSerializer,
)
from humotech.offices.services import OfficeService


class OfficeViewSet(ServiceViewSet):
    service_class = OfficeService
    read_serializer_class = OfficeSerializer

    def list(self, request):
        params = self.list_params()
        # Кривой UUID — 400, а не 500 из ORM.
        region_id = query_uuid(request.query_params, "region_id")
        if region_id:
            params["region_id"] = region_id
        return self.page_response(self.service.list(self.actor, **params))

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    def create(self, request):
        payload = validated(OfficeCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True
        )

    def partial_update(self, request, pk=None):
        payload = validated(OfficeUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(self.service.deactivate(self.actor, pk))

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self.item_response(self.service.reactivate(self.actor, pk))

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Закрытие насовсем — отдельное действие, а не смена статуса правкой:
        обратной операции у него нет, и путать её с деактивацией нельзя."""
        payload = validated(OfficeCloseSerializer, request.data)
        return self.item_response(
            self.service.close(self.actor, pk, closed_at=payload.get("closed_at"))
        )
