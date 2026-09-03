"""REST-интерфейс справочника регионов.

Слой тонкий: разобрать запрос, вызвать сервис, отдать результат. Проверки
прав и области видимости остаются в сервисе — иначе Telegram-бот и команды
обслуживания обошли бы их молча.
"""

from __future__ import annotations

from rest_framework.decorators import action

from humotech.core.api import ServiceViewSet, validated
from humotech.regions.serializers import (
    RegionCreateSerializer,
    RegionSerializer,
    RegionUpdateSerializer,
)
from humotech.regions.services import RegionService


class RegionViewSet(ServiceViewSet):
    service_class = RegionService
    read_serializer_class = RegionSerializer

    def list(self, request):
        return self.page_response(
            self.service.list(self.actor, **self.list_params())
        )

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    def create(self, request):
        payload = validated(RegionCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True
        )

    def partial_update(self, request, pk=None):
        payload = validated(RegionUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(self.service.deactivate(self.actor, pk))

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self.item_response(self.service.reactivate(self.actor, pk))
