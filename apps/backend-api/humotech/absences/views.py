"""REST-интерфейс отдела кадров для заявок на отсутствие.

Без React: интерфейс делается следующим этапом, а подтверждать больничные
надо уже сейчас. Здесь только то, без чего сотруднический сценарий
не замыкается: посмотреть очередь, подтвердить, отклонить, отменить
подтверждённое.

Аутентификация — сессия CRM, и только она. Ни `MiniAppAuthentication`,
ни ботовая сюда не подключены: `EmployeePrincipal` намеренно не умеет
строить `Actor`, и попытка пройти сюда токеном сотрудника упадёт, а не
отдаст тихо чужие данные. Это свойство поддерживается тем, что классы
аутентификации здесь не перечислены вовсе — работают умолчания проекта.
"""

from __future__ import annotations

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.absences.services import AbsenceService
from humotech.core.api import validated
from humotech.core.rbac import Actor


class DecisionSerializer(serializers.Serializer):
    comment = serializers.CharField(
        max_length=2000, required=False, allow_blank=True
    )


def hr_request_json(request) -> dict:
    """Заявка глазами кадровика.

    Комментарий сотрудника здесь есть: тот, кто принимает решение, должен
    видеть, о чём его просят. Диагноза в нём быть не должно, и подсказка
    об этом стоит в самом поле ввода в приложении.
    """
    return {
        "id": str(request.id),
        "employee": {
            "id": str(request.employee_id),
            "full_name": " ".join(
                part for part in (
                    request.employee.last_name,
                    request.employee.first_name,
                    request.employee.middle_name,
                ) if part
            ),
            "employee_number": request.employee.employee_number,
        },
        "kind": request.request_kind,
        "parent_request_id": (
            str(request.parent_request_id) if request.parent_request_id else None
        ),
        "absence_type": {
            "code": request.absence_type.code,
            "name": request.absence_type.name,
        },
        "status": request.status,
        "first_day": (
            request.requested_start_at.date().isoformat()
            if request.requested_start_at else None
        ),
        "last_day": (
            request.requested_end_at.date().isoformat()
            if request.requested_end_at else None
        ),
        "comment": request.employee_comment,
        "review_comment": request.review_comment,
        "submitted_at": (
            request.submitted_at.isoformat() if request.submitted_at else None
        ),
    }


class PendingAbsenceRequestsView(APIView):
    """Очередь заявок, ожидающих решения."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = Actor.from_user(request.user)
        rows = AbsenceService().pending(actor)
        return Response({"requests": [hr_request_json(row) for row in rows]})


class AbsenceDecisionView(APIView):
    """Подтвердить, отклонить или отменить подтверждённое.

    Отмена подтверждённого — отдельное действие, а не «отклонить ещё раз»:
    отсутствие уже существует и попало в статистику, и снимать его нужно
    вместе с ним.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, request_id, decision):
        actor = Actor.from_user(request.user)
        data = validated(DecisionSerializer, request.data)
        comment = data.get("comment") or None
        service = AbsenceService()

        if decision == "cancel":
            row = service.cancel_approved(actor, request_id, comment=comment)
        else:
            row = service.decide(
                actor, request_id, approve=decision == "approve", comment=comment
            )
        return Response(hr_request_json(row))


__all__ = ["AbsenceDecisionView", "PendingAbsenceRequestsView"]
