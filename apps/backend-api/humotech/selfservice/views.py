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

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.attendance import statistics
from humotech.attendance import reminders
from humotech.attendance.scanning import scan
from humotech.core.api import validated
from humotech.core.clientip import client_ip
from humotech.selfservice.presentation import (
    day_json,
    session_json,
    status_json,
    summary_json,
)
from humotech.selfservice.responses import (
    CurrentStatusSerializer,
    HistorySerializer,
    ProfileSerializer,
    ScanResultSerializer,
    StatisticsSerializer,
)
from humotech.selfservice.serializers import (
    DayNoticeSerializer,
    PeriodSerializer,
    ScanRequestSerializer,
)
from humotech.selfservice.throttling import EmployeeRateThrottle, ScanRateThrottle
from humotech.onboarding.flow import OnboardingFlow
from humotech.onboarding.permissions import OnboardingCompleted
from humotech.telegram.auth import (
    BotEmployeeAuthentication,
    IsLinkedEmployee,
    MiniAppAuthentication,
)


class EmployeeSelfView(APIView):
    """Общее основание для всех экранов сотрудника.

    Держит в одном месте то, что иначе пришлось бы повторять в каждом view
    и однажды забыть: два способа входа, требование живой привязки,
    ограничение частоты и гейт первичного ознакомления.

    `onboarding_gate = False` открывает экран и до завершения
    ознакомления. Таких экранов ровно два вида: профиль — он отвечает
    на вопрос «кто я», и без него бот не смог бы даже завести разговор
    об ознакомлении, — и сами шаги ознакомления.
    """

    authentication_classes = [MiniAppAuthentication, BotEmployeeAuthentication]
    permission_classes = [IsLinkedEmployee, OnboardingCompleted]
    throttle_classes = [EmployeeRateThrottle]
    #: Закрыт ли экран до завершения ознакомления.
    onboarding_gate = True

    @property
    def context(self):
        return self.request.user.context


@extend_schema(tags=["Личный кабинет"])
class ProfileView(EmployeeSelfView):
    """Кто я и где я числюсь.

    Отдаёт ровно то, что человек и так про себя знает. Ни идентификаторов
    чужих сотрудников, ни данных руководителя, ни оклада здесь нет: экран
    существует, чтобы человек убедился, что система видит его правильно.

    Гейтом ознакомления НЕ закрыт. Бот спрашивает этот адрес на каждом
    обновлении, чтобы понять, с кем разговаривает; закрыв профиль, мы
    закрыли бы и разговор про само ознакомление. Вместо отказа профиль
    несёт блок `onboarding` — по нему бот и решает, какое меню показать.
    """

    onboarding_gate = False

    @extend_schema(
        operation_id="me_profile",
        summary="Мой профиль",
        responses={200: ProfileSerializer},
    )
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
                # Что человеку сейчас доступно. Бот собирает меню по
                # этому блоку, а не по собственной памяти: кнопка,
                # нарисованная по вчерашнему состоянию, обещала бы то,
                # на что сервер ответит отказом.
                "onboarding": _onboarding_block(employee),
            }
        )


@extend_schema(tags=["Личный кабинет"])
class ScanView(EmployeeSelfView):
    """Отметка по QR.

    Тело запроса — один код и, необязательно, идентификатор попытки от
    клиента. Ни офиса, ни направления, ни времени: всё это решает сервер.
    Направление особенно — прислать «я выхожу» нельзя, потому что такого
    параметра нет.
    """

    throttle_classes = [ScanRateThrottle]

    @extend_schema(
        operation_id="me_scan",
        summary="Отметка по QR",
        description=(
            "Ни офиса, ни направления, ни времени в запросе нет. "
            "Направление особенно: прислать «я выхожу» нельзя, потому "
            "что такого параметра не существует — сервер решает сам "
            "по последней отметке."
        ),
        request=ScanRequestSerializer,
        responses={200: ScanResultSerializer},
    )
    def post(self, request):
        data = validated(ScanRequestSerializer, request.data)
        outcome = scan(
            self.context,
            token=data["token"],
            ip_address=client_ip(request),
            client_event_id=data.get("client_event_id"),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            accuracy_m=data.get("accuracy_m"),
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
                "occurred_at_local": _local_time(
                    outcome.occurred_at, outcome.office_timezone
                ),
                "point_mode": outcome.point_mode,
                "distance_m": (
                    round(outcome.distance_m) if outcome.distance_m is not None
                    else None
                ),
                "radius_m": outcome.radius_m,
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


def _local_time(moment, zone: str | None) -> str | None:
    """«09:02» в часовом поясе офиса.

    Считает сервер, а не бот и не телефон: у них нет ни пояса офиса, ни
    права решать, который час был при отметке.
    """
    if moment is None:
        return None
    try:
        local = moment.astimezone(ZoneInfo(zone)) if zone else moment
    except (ZoneInfoNotFoundError, ValueError):
        local = moment
    return local.strftime("%H:%M")


@extend_schema(tags=["Личный кабинет"])
class DayNoticeView(EmployeeSelfView):
    """Ответ на напоминание о начале дня.

    Это НЕ заявка. «Не приду» не оформляет ни отпуска, ни больничного:
    они проходят согласование и живут своими адресами. Здесь человек
    только объясняет пустую строку в табеле, и кадровик видит разницу
    между «предупредил» и «пропал».

    Строка одна на человека и день: сказавший «опаздываю», а потом «не
    приду», обновляет прежний ответ, а не заводит второй.
    """

    @extend_schema(
        operation_id="me_day_notice",
        summary="Опаздываю или не приду",
        request=DayNoticeSerializer,
        responses={200: DayNoticeSerializer},
    )
    def post(self, request):
        data = validated(DayNoticeSerializer, request.data)
        row = reminders.notice(
            employee=self.context.employee,
            kind=data["kind"],
            comment=data.get("comment"),
        )
        return Response(
            {"kind": row.kind, "comment": row.comment, "day": row.day.isoformat()}
        )


@extend_schema(tags=["Личный кабинет"])
class StatusView(EmployeeSelfView):
    """Главный экран: где человек сейчас и что у него сегодня."""

    @extend_schema(
        operation_id="me_status",
        summary="Где я сейчас",
        responses={200: CurrentStatusSerializer},
    )
    def get(self, request):
        return Response(status_json(statistics.current_status(self.context)))


@extend_schema(tags=["Личный кабинет"])
class StatisticsView(EmployeeSelfView):
    """Статистика за сегодня, неделю, месяц или произвольный период.

    Период задаётся датами в поясе офиса, а не моментами времени: человек
    спрашивает «за сентябрь», и в каком часовом поясе начался сентябрь —
    вопрос, на который отвечает сервер, а не клиент.
    """

    @extend_schema(
        operation_id="me_statistics",
        summary="Моя статистика",
        parameters=[PeriodSerializer],
        responses={200: StatisticsSerializer},
    )
    def get(self, request):
        params = validated(PeriodSerializer, request.query_params)
        report = _report(self.context, params)
        return Response(
            {
                "summary": summary_json(report),
                # Дневная разбивка идёт вместе с итогом: иначе клиенту
                # пришлось бы просить её отдельным запросом и складывать
                # самому — то есть считать статистику второй раз.
                "days": [day_json(day) for day in report.days],
            }
        )


@extend_schema(tags=["Личный кабинет"])
class HistoryView(EmployeeSelfView):
    """История посещений: день за днём, с каждым входом и выходом.

    Порядок обратный — от свежего к старому: человек смотрит историю,
    чтобы проверить вчерашнее, а не позапрошлогоднее.
    """

    MAX_DAYS = 62

    @extend_schema(
        operation_id="me_history",
        summary="Моя история посещений",
        description=(
            "Показываются только дни, о которых есть что сказать: "
            "пустые выходные посреди истории — это шум, через который "
            "приходится прокручивать."
        ),
        parameters=[
            PeriodSerializer,
            OpenApiParameter("offset", OpenApiTypes.INT),
            OpenApiParameter(
                "limit", OpenApiTypes.INT,
                description="не больше 62 дней за раз",
            ),
        ],
        responses={200: HistorySerializer},
    )
    def get(self, request):
        params = validated(PeriodSerializer, request.query_params)
        report = _report(self.context, params)

        offset = max(int(request.query_params.get("offset") or 0), 0)
        limit = min(max(int(request.query_params.get("limit") or 31), 1),
                    self.MAX_DAYS)

        # Показываем только дни, о которых есть что сказать: пустые
        # выходные посреди истории — это шум, через который приходится
        # прокручивать.
        meaningful = [
            day for day in reversed(report.days)
            if day.sessions or day.absence_code is not None or day.missed
        ]
        page = meaningful[offset:offset + limit]

        return Response(
            {
                "period": {
                    "first": report.first.isoformat(),
                    "last": report.last.isoformat(),
                    "timezone": report.timezone,
                },
                "days": [
                    {
                        **day_json(day),
                        "sessions": [session_json(s) for s in day.sessions],
                    }
                    for day in page
                ],
                "total": len(meaningful),
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < len(meaningful),
            }
        )


def _report(context, params):
    """Готовый период по имени либо произвольный по датам."""
    period = params.get("period")
    if period == "today":
        return statistics.for_today(context)
    if period == "week":
        return statistics.for_week(context)
    if period == "month":
        return statistics.for_month(context)
    return statistics.for_period(context, params["date_from"], params["date_to"])


def _onboarding_block(employee) -> dict:
    """Состояние ознакомления в ответе профиля.

    `enrolled=False` — человека в программу не звали: он работает как
    прежде, и никаких кнопок про ознакомление ему показывать не надо.
    """
    progress = OnboardingFlow().state_or_none(employee.id)
    if progress is None:
        return {
            "enrolled": False, "required": False, "completed": True,
            "status": None, "stage": "DONE",
            "sections_done": 0, "sections_total": 0,
            "policies_done": 0, "policies_total": 0,
        }
    return {
        "enrolled": True,
        # «Требуется» и «не завершено» — одно и то же, но у клиента это
        # разные вопросы: один про меню, другой про прогресс.
        "required": not progress.completed,
        "completed": progress.completed,
        "status": progress.status,
        "stage": progress.stage,
        "sections_done": progress.sections_done,
        "sections_total": progress.sections_total,
        "policies_done": progress.policies_done,
        "policies_total": progress.policies_total,
    }


def full_name(employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


__all__ = [
    "EmployeeSelfView",
    "HistoryView",
    "ProfileView",
    "ScanView",
    "StatisticsView",
    "StatusView",
    "full_name",
]
