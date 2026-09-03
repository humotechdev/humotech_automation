"""REST-интерфейс кадровых операций."""

from __future__ import annotations

from datetime import date

from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.core.api import ServiceViewSet, validated
from humotech.core.errors import ValidationFailed
from humotech.employees.serializers import (
    AssignmentChangeSerializer,
    AssignmentSerializer,
    EmployeeCardSerializer,
    EmployeeCreateSerializer,
    EmployeeListItemSerializer,
    EmployeeUpdateSerializer,
    TerminateSerializer,
)
from humotech.employees.services import EmployeeService


class EmployeeViewSet(ServiceViewSet):
    service_class = EmployeeService
    read_serializer_class = EmployeeCardSerializer

    # ------------------------------------------------------------------ чтение

    def list(self, request):
        params = self.list_params()
        for name in ("office_id", "region_id"):
            value = request.query_params.get(name)
            if value:
                params[name] = value
        params["at"] = self._at()
        return self.page_response(
            self.service.list(self.actor, **params),
            serializer_class=EmployeeListItemSerializer,
        )

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk, at=self._at()))

    @action(detail=True, methods=["get"], url_path="assignments")
    def assignments(self, request, pk=None):
        """История переводов: где, кем и в какой период человек работал."""
        history = self.service.assignment_history(self.actor, pk)
        return Response({"items": AssignmentSerializer(history, many=True).data})

    def _at(self) -> date | None:
        """Дата, на которую смотрим состав.

        Нужна, потому что назначения хранятся периодами: «где работает
        сотрудник» — вопрос, у которого нет ответа без даты.
        """
        raw = self.request.query_params.get("at")
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationFailed(
                "Параметр «at» должен быть датой в формате ГГГГ-ММ-ДД",
                details={"at": raw},
            ) from exc

    # --------------------------------------------------------------- изменение

    def create(self, request):
        payload = validated(EmployeeCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True
        )

    def partial_update(self, request, pk=None):
        payload = validated(EmployeeUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))

    @action(detail=True, methods=["post"], url_path="change-assignment")
    def change_assignment(self, request, pk=None):
        """Перевод в другой офис, отдел или на другую должность.

        Отдельное действие, а не правка карточки: перевод создаёт НОВЫЙ период
        и закрывает прежний, а PATCH по смыслу означал бы правку одной строки.
        """
        payload = validated(AssignmentChangeSerializer, request.data)
        assignment = self.service.change_assignment(self.actor, pk, **payload)
        return self.item_response(
            assignment, created=True, serializer_class=AssignmentSerializer
        )

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(self.service.deactivate(self.actor, pk))

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self.item_response(self.service.reactivate(self.actor, pk))

    @action(detail=True, methods=["post"])
    def terminate(self, request, pk=None):
        """Увольнение. Запись и вся история сохраняются, открытые периоды
        закрываются датой увольнения."""
        payload = validated(TerminateSerializer, request.data)
        return self.item_response(
            self.service.terminate(
                self.actor, pk,
                termination_date=payload["termination_date"],
                reason=payload.get("reason"),
            )
        )
