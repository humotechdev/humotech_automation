"""REST-интерфейс сводных показателей."""

from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.analytics.dashboard import DashboardService
from humotech.attendance.views import _date_param, _uuid_param
from humotech.core.rbac import Actor


class DashboardView(APIView):
    """Главная страница CRM.

    Один запрос вместо тринадцати: карточки считаются из одной выборки
    присутствия, и число на карточке гарантированно совпадает со списком,
    который откроется по её адресу.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = Actor.from_user(request.user)
        summary = DashboardService().summary(
            actor,
            day=_date_param(request, "date"),
            office_id=_uuid_param(request, "office_id"),
            region_id=_uuid_param(request, "region_id"),
            department_id=_uuid_param(request, "department_id"),
            position_id=_uuid_param(request, "position_id"),
            schedule_id=_uuid_param(request, "schedule_id"),
        )
        return Response(
            {
                "date": summary.date.isoformat(),
                "timezone": summary.timezone,
                "cards": [
                    {
                        "key": card.key,
                        "title": card.title,
                        "value": card.value,
                        "endpoint": card.endpoint,
                        "params": card.params,
                        "attention": card.attention,
                    }
                    for card in summary.cards
                ],
                "warnings": summary.warnings,
            }
        )
