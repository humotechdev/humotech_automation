"""REST-интерфейс сводных показателей."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.analytics.dashboard import DashboardService
from humotech.analytics.metrics import AnalyticsService
from humotech.attendance.views import _date_param, _uuid_param
from humotech.core.rbac import Actor


# Схемы ответов. Нужны генератору OpenAPI: без них фронтенд получает
# документацию, в которой у половины эндпоинтов не описано тело ответа.
# В коде эти классы не используются — ответы собираются вручную.


class CardSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    value = serializers.IntegerField()
    endpoint = serializers.CharField(
        allow_null=True,
        help_text="Адрес списка, из которого сложилось число",
    )
    params = serializers.DictField(
        help_text="Параметры к этому адресу — подставлять как есть",
    )
    attention = serializers.BooleanField()


class DashboardResponseSerializer(serializers.Serializer):
    date = serializers.DateField()
    timezone = serializers.CharField()
    cards = CardSerializer(many=True)
    warnings = serializers.ListField(child=serializers.DictField())


class RatioSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    percent = serializers.FloatField(
        allow_null=True,
        help_text="null при нулевом знаменателе — это НЕ ноль процентов",
    )
    numerator = serializers.FloatField()
    denominator = serializers.FloatField()
    formula = serializers.CharField(help_text="Что именно делится на что")
    unit = serializers.ChoiceField(choices=["days", "hours", "people"])


class AnalyticsResponseSerializer(serializers.Serializer):
    scope = serializers.DictField()
    period = serializers.DictField()
    generated_at = serializers.DateTimeField()
    headcount = serializers.IntegerField()
    coverage = serializers.DictField()
    totals = serializers.DictField()
    ratios = RatioSerializer(many=True)
    series = serializers.ListField(child=serializers.DictField(), required=False)


class ComparisonResponseSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["region", "office", "period"])
    left = AnalyticsResponseSerializer()
    right = AnalyticsResponseSerializer()
    differences = serializers.ListField(child=serializers.DictField())


PERIOD_PARAMS = [
    OpenApiParameter("date_from", str, description="Начало периода, ГГГГ-ММ-ДД"),
    OpenApiParameter("date_to", str, description="Конец периода включительно"),
]


class DashboardView(APIView):
    """Главная страница CRM.

    Один запрос вместо тринадцати: карточки считаются из одной выборки
    присутствия, и число на карточке гарантированно совпадает со списком,
    который откроется по её адресу.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Главная страница CRM",
        description=(
            "Карточки на выбранную дату. У каждой есть endpoint и params: "
            "подставьте их как есть, и получите список, из которого "
            "сложилось число. Собирать адрес на клиенте не нужно — "
            "собранный вручную однажды разойдётся с числом."
        ),
        parameters=[
            OpenApiParameter("date", str, description="День, ГГГГ-ММ-ДД"),
            OpenApiParameter("office_id", str),
            OpenApiParameter("region_id", str),
            OpenApiParameter("department_id", str),
            OpenApiParameter("position_id", str),
            OpenApiParameter("schedule_id", str),
        ],
        responses=DashboardResponseSerializer,
        tags=["Дашборд"],
    )
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


class AnalyticsView(APIView):
    """Показатели по организации, региону, офису или сотруднику.

    Область задаётся параметрами `region_id` / `office_id` / `employee_id`;
    без них считается вся видимая область. Ни один из них не расширяет
    доступ — все три проходят проверку области видимости.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Показатели посещаемости",
        description=(
            "Каждая доля приходит с числителем, знаменателем и словесным "
            "определением формулы. percent = null означает «нет данных» "
            "(нулевой знаменатель), а не ноль процентов. Показатели по "
            "конкретному сотруднику требуют ещё и employees.read."
        ),
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter("region_id", str),
            OpenApiParameter("office_id", str),
            OpenApiParameter(
                "employee_id", str,
                description="Требует отдельного разрешения employees.read",
            ),
            OpenApiParameter(
                "series", bool,
                description="false — не считать временной ряд по дням",
            ),
        ],
        responses=AnalyticsResponseSerializer,
        tags=["Аналитика"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        first, last = _period(request)
        service = AnalyticsService()

        employee_id = _uuid_param(request, "employee_id")
        office_id = _uuid_param(request, "office_id")
        region_id = _uuid_param(request, "region_id")

        if employee_id:
            report = service.employee(actor, employee_id, first=first, last=last)
        elif office_id:
            report = service.office(actor, office_id, first=first, last=last)
        elif region_id:
            report = service.region(actor, region_id, first=first, last=last)
        else:
            report = service.organization(actor, first=first, last=last)

        return Response(report.as_dict(with_series=_series_wanted(request)))


class ComparisonView(APIView):
    """Сравнение двух областей или двух периодов.

    Обе стороны возвращаются целиком, а не только разница: увидев «+12»,
    кадровик сразу спросит «двенадцать от чего», и ответ должен быть
    в том же ответе сервера.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Сравнение областей или периодов",
        description=(
            "Разница отдаётся в процентных ПУНКТАХ: 95% против 90% — это "
            "5 пунктов, а не 5 процентов. comparable = false означает, что "
            "у одной из сторон нулевой знаменатель и разница не определена."
        ),
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter(
                "kind", str, enum=["region", "office", "period"],
                description="Что сравнивается; по умолчанию office",
            ),
            OpenApiParameter("left_id", str),
            OpenApiParameter("right_id", str),
            OpenApiParameter(
                "right_first", str, description="Начало второго периода",
            ),
            OpenApiParameter(
                "right_last", str, description="Конец второго периода",
            ),
        ],
        responses=ComparisonResponseSerializer,
        tags=["Аналитика"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        first, last = _period(request)
        result = AnalyticsService().compare(
            actor,
            kind=request.query_params.get("kind") or "office",
            left_id=_uuid_param(request, "left_id"),
            right_id=_uuid_param(request, "right_id"),
            first=first,
            last=last,
            right_first=_date_param(request, "right_first"),
            right_last=_date_param(request, "right_last"),
        )
        return Response(result)


def _period(request):
    """Период запроса. По умолчанию — текущий месяц.

    Умолчание названо здесь, а не подразумевается: «за какой период эта
    цифра» — первый вопрос к любому показателю, и период всегда приходит
    в ответе вместе с числами.
    """
    from datetime import date as _date

    from humotech.core.timeframes import month_range

    first = _date_param(request, "date_from")
    last = _date_param(request, "date_to")
    if first and last:
        return first, last
    default_first, default_last = month_range(_date.today())
    return first or default_first, last or default_last


def _series_wanted(request) -> bool:
    raw = request.query_params.get("series")
    return raw is None or raw.lower() not in ("0", "false", "no")
