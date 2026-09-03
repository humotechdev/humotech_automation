"""REST-интерфейс рабочих графиков."""

from __future__ import annotations

from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.core.api import ServiceViewSet, validated
from humotech.schedules.serializers import (
    ScheduleAssignSerializer,
    ScheduleAssignmentSerializer,
    WorkScheduleCreateSerializer,
    WorkScheduleDetailSerializer,
    WorkScheduleSerializer,
    WorkScheduleUpdateSerializer,
    to_day_specs,
)
from humotech.schedules.services import WorkScheduleService


class WorkScheduleViewSet(ServiceViewSet):
    service_class = WorkScheduleService
    read_serializer_class = WorkScheduleSerializer

    def list(self, request):
        return self.page_response(
            self.service.list(self.actor, **self.list_params())
        )

    def retrieve(self, request, pk=None):
        return self.item_response(
            self.service.get(self.actor, pk),
            serializer_class=WorkScheduleDetailSerializer,
        )

    def create(self, request):
        payload = validated(WorkScheduleCreateSerializer, request.data)
        payload["days"] = to_day_specs(payload.get("days"))
        return self.item_response(
            self.service.create(self.actor, **payload), created=True,
            serializer_class=WorkScheduleDetailSerializer,
        )

    def partial_update(self, request, pk=None):
        payload = validated(WorkScheduleUpdateSerializer, request.data)
        if "days" in payload:
            payload["days"] = to_day_specs(payload["days"])
        return self.item_response(
            self.service.update(self.actor, pk, **payload),
            serializer_class=WorkScheduleDetailSerializer,
        )

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(self.service.deactivate(self.actor, pk))

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self.item_response(self.service.reactivate(self.actor, pk))

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        """Назначить график сотруднику с указанной даты.

        Действие на графике, а не на сотруднике: право здесь
        `schedules.manage`, и в одном месте видно, кому график достался.
        """
        payload = validated(ScheduleAssignSerializer, request.data)
        assignment = self.service.assign_to_employee(
            self.actor,
            employee_id=payload["employee_id"],
            schedule_id=pk,
            valid_from=payload["valid_from"],
        )
        return self.item_response(
            assignment, created=True,
            serializer_class=ScheduleAssignmentSerializer,
        )


class EmployeeScheduleViewSet(ServiceViewSet):
    """История графиков конкретного сотрудника."""

    service_class = WorkScheduleService
    read_serializer_class = ScheduleAssignmentSerializer

    def list(self, request, employee_pk=None):
        history = self.service.history(self.actor, employee_pk)
        return Response({"items": self.read_serializer_class(history, many=True).data})
