"""Endpoint'ы личного кабинета.

Каждый view здесь начинается одинаково: `request.user.context` — уже
проверенный `EmployeeContext`. Ни один из них не читает `employee_id` или
`organization_id` из запроса, и это не соглашение, а свойство устройства:
таких параметров просто нет ни в одном пути и ни в одном теле.

Аутентификаций две — Mini App и бот, — а код один. Порядок в списке значения
не имеет: классы различают себя по заголовкам и не перехватывают чужие
запросы.
"""

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.attendance.scanning import scan
from humotech.core.api import validated
from humotech.core.clientip import client_ip
from humotech.selfservice.serializers import ScanRequestSerializer
from humotech.selfservice.throttling import EmployeeRateThrottle, ScanRateThrottle
from humotech.telegram.auth import (
    BotEmployeeAuthentication,
    IsLinkedEmployee,
    MiniAppAuthentication,
)


class EmployeeSelfView(APIView):
    """Общее основание для всех экранов сотрудника.

    Держит в одном месте то, что иначе пришлось бы повторять в каждом view
    и однажды забыть: два способа входа, требование живой привязки
    и ограничение частоты.
    """

    authentication_classes = [MiniAppAuthentication, BotEmployeeAuthentication]
    permission_classes = [IsLinkedEmployee]
    throttle_classes = [EmployeeRateThrottle]

    @property
    def context(self):
        return self.request.user.context


class ProfileView(EmployeeSelfView):
    """Кто я и где я числюсь.

    Отдаёт ровно то, что человек и так про себя знает. Ни идентификаторов
    чужих сотрудников, ни данных руководителя, ни оклада здесь нет: экран
    существует, чтобы человек убедился, что система видит его правильно.
    """

    def get(self, request):
        context = self.context
        employee = context.employee
        assignment = context.assignment
        office = context.office

        return Response(
            {
                "employee": {
                    "id": str(employee.id),
                    "full_name": full_name(employee),
                    "employee_number": employee.employee_number,
                    "employment_status": employee.employment_status,
                    "preferred_language": employee.preferred_language,
                },
                "office": {
                    "id": str(office.id),
                    "name": office.name,
                    # Пояс отдаётся клиенту, чтобы он не считал сутки сам,
                    # а показывал уже посчитанное сервером и знал, в каком
                    # поясе подписывать время.
                    "timezone": office.timezone,
                },
                "position": (
                    {
                        "id": str(assignment.position_id),
                        "name": assignment.position.name,
                    }
                    if assignment.position_id
                    else None
                ),
                "department": (
                    {
                        "id": str(assignment.department_id),
                        "name": assignment.department.name,
                    }
                    if assignment.department_id
                    else None
                ),
                "assignment": {
                    "employment_type": assignment.employment_type,
                    "work_mode": assignment.work_mode,
                    "valid_from": assignment.valid_from.isoformat(),
                },
                "telegram": {
                    "status": context.account.status,
                    "username": context.account.telegram_username,
                },
            }
        )


class ScanView(EmployeeSelfView):
    """Отметка по QR.

    Тело запроса — один код и, необязательно, идентификатор попытки от
    клиента. Ни офиса, ни направления, ни времени: всё это решает сервер.
    Направление особенно — прислать «я выхожу» нельзя, потому что такого
    параметра нет.
    """

    throttle_classes = [ScanRateThrottle]

    def post(self, request):
        data = validated(ScanRequestSerializer, request.data)
        outcome = scan(
            self.context,
            token=data["token"],
            ip_address=client_ip(request),
            client_event_id=data.get("client_event_id"),
        )
        session = outcome.session
        return Response(
            {
                "status": outcome.status,
                "accepted": outcome.accepted,
                "office_name": outcome.office_name,
                "point_name": outcome.point_name,
                "occurred_at": (
                    outcome.occurred_at.isoformat() if outcome.occurred_at else None
                ),
                "session": (
                    {
                        "id": str(session.id),
                        "started_at": session.started_at.isoformat(),
                        "ended_at": (
                            session.ended_at.isoformat() if session.ended_at else None
                        ),
                        "duration_seconds": session.duration_seconds,
                        "status": session.status,
                    }
                    if session is not None
                    else None
                ),
            }
        )


def full_name(employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


__all__ = ["EmployeeSelfView", "ProfileView", "ScanView", "full_name"]
