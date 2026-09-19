"""REST-интерфейс сводных показателей."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.analytics.dashboard import DashboardService
from humotech.analytics.metrics import AnalyticsService
from humotech.analytics.overview import OverviewService
from humotech.core.errors import ValidationFailed
from humotech.attendance.views import _date_param, _uuid_param
from humotech.analytics.movement import MovementService
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


class OverviewResponseSerializer(serializers.Serializer):
    period = serializers.DictField()
    previous_period = serializers.DictField()
    weekday = serializers.IntegerField(allow_null=True)
    generated_at = serializers.DateTimeField()
    timezones = serializers.ListField(child=serializers.CharField())
    summary = serializers.DictField()
    days = serializers.ListField(child=serializers.DictField())
    previous_days = serializers.ListField(child=serializers.DictField())
    offices = serializers.ListField(child=serializers.DictField())
    # Та же явка уровнем выше: регионы собираются из своих офисов, а не
    # считаются отдельно — два подсчёта одного числа однажды разойдутся.
    regions = serializers.ListField(child=serializers.DictField())
    # Рейтинг людей: худшая явка сверху. Страницу открывают, чтобы найти
    # проблему, а не полюбоваться отличниками.
    employees = serializers.ListField(child=serializers.DictField())
    arrivals = serializers.DictField()
    weekdays = serializers.DictField()


class MovementSpanSerializer(serializers.Serializer):
    first = serializers.DateField()
    last = serializers.DateField()
    hired = serializers.IntegerField()
    left = serializers.IntegerField()
    difference = serializers.IntegerField()


class MovementResponseSerializer(serializers.Serializer):
    current = MovementSpanSerializer()
    previous = MovementSpanSerializer()
    month_before = MovementSpanSerializer()
    year_before = MovementSpanSerializer()
    headcount = serializers.IntegerField()


class MovementView(APIView):
    """Движение сотрудников: принято, уволено, разница.

    Отдельно от посещаемости: там единица измерения — дни, здесь —
    люди, и складывать их в одном ответе значит путать два разных
    вопроса.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Движение сотрудников",
        description=(
            "Приём считается по дате выхода, увольнение — по дате "
            "увольнения, а не по дате создания карточки: человека "
            "оформляют заранее. Сравнение идёт с равным по длине "
            "предыдущим периодом, а также с тем же периодом месяцем и "
            "годом раньше."
        ),
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter("region_id", str),
            OpenApiParameter("office_id", str),
        ],
        responses=MovementResponseSerializer,
        tags=["Аналитика"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        first, last = _period(request)
        report = MovementService().report(
            actor,
            first=first,
            last=last,
            office_id=_uuid_param(request, "office_id"),
            region_id=_uuid_param(request, "region_id"),
        )
        return Response({
            "current": _movement_json(report.current),
            "previous": _movement_json(report.previous),
            "month_before": _movement_json(report.month_before),
            "year_before": _movement_json(report.year_before),
            "headcount": report.headcount,
        })


def _movement_json(row) -> dict:
    return {
        "first": row.first.isoformat(),
        "last": row.last.isoformat(),
        "hired": row.hired,
        "left": row.left,
        "difference": row.difference,
    }


class AnalyticsOverviewView(APIView):
    """Всё для страницы «Аналитика» одним ответом.

    Сводка с предыдущим равным периодом, каждый день периода, рейтинг
    офисов, ритм прихода и дни недели. Правила — те же, что у `/analytics`.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Обзор аналитики",
        description=(
            "Доли приходят с числителем и знаменателем; percent = null — "
            "нулевой знаменатель, а не ноль процентов. Выходные, дни без "
            "графика, оформленные отсутствия и будущие дни в неявку не "
            "входят. Среднее время — только по закрытым посещениям. Сдвиг "
            "прихода считается от начала личной смены сотрудника, сутки — в "
            "поясе его офиса."
        ),
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter("region_id", str),
            OpenApiParameter("office_id", str),
            OpenApiParameter("department_id", str),
            OpenApiParameter("employee_id", str),
            OpenApiParameter(
                "weekday", int,
                description="Детализация по дню недели: 1 — понедельник … 7",
            ),
        ],
        responses=OverviewResponseSerializer,
        tags=["Аналитика"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        first, last = _period(request)
        raw = request.query_params.get("weekday")
        try:
            weekday = int(raw) if raw else None
        except ValueError:
            raise ValidationFailed(
                "День недели — число от 1 до 7", details={"weekday": raw}
            ) from None
        body = OverviewService().overview(
            actor,
            first=first,
            last=last,
            region_id=_uuid_param(request, "region_id"),
            office_id=_uuid_param(request, "office_id"),
            department_id=_uuid_param(request, "department_id"),
            employee_id=_uuid_param(request, "employee_id"),
            weekday=weekday,
        )
        return Response(body)


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
