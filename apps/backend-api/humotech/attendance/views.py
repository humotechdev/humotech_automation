"""REST-интерфейс посещаемости для CRM.

Слой тонкий, как и весь REST в проекте: разобрать параметры, вызвать
сервис, отдать результат. Права и область видимости проверяет сервис —
если бы это делал view, любой другой вызывающий обошёл бы проверку молча.

Параметры фильтрации перечислены поимённо и нигде не раскрываются из
`request.query_params` целиком. Это не занудство: `organization_id`,
пришедший от клиента, не должен иметь ни одного способа попасть в запрос
к базе, и перечисление руками — единственная защита, которая не ломается
при добавлении поля.
"""

from __future__ import annotations

import uuid
from datetime import date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers as drf_serializers
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.attendance.hr import PRESENCE_STATES, AttendanceHrService
from humotech.attendance.serializers import (
    AttendanceEventSerializer,
    AttendanceSessionSerializer,
    CorrectionDecisionSerializer,
    CorrectionRequestSerializer,
    ManualEventSerializer,
    PresenceRowSerializer,
)
from humotech.core.api import ServiceViewSet, validated
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor


# Потолок числа строк в одном ответе присутствия.
#
# У экрана присутствия нет страниц намеренно (см. `AttendanceHrService.
# presence`), а «размер ограничен областью видимости» — правда только для
# администратора офиса. У HR_ADMIN область — вся организация, и без
# потолка ответ рос бы вместе с компанией без предела.
#
# Обрезаются только строки ответа; `counts` считается по всему набору,
# иначе карточка «не пришли: 7» начала бы врать ровно на большом штате.
# Дашборд и выгрузка берут набор целиком, мимо этого потолка.
PRESENCE_MAX_ROWS = 500

# Параметры, общие для журналов. Перечислены здесь один раз: схема
# и код читают один и тот же список, и разъехаться им негде.
SCOPE_PARAMS = [
    OpenApiParameter("employee_id", str, description="Один сотрудник"),
    OpenApiParameter("office_id", str, description="Один офис"),
    OpenApiParameter("region_id", str, description="Все офисы региона"),
    OpenApiParameter("date_from", str, description="Начало периода, ГГГГ-ММ-ДД"),
    OpenApiParameter("date_to", str, description="Конец периода включительно"),
    OpenApiParameter("cursor", str, description="Курсор следующей страницы"),
    OpenApiParameter("limit", int, description="Размер страницы, до 200"),
]


class PresenceResponseSerializer(drf_serializers.Serializer):
    """Ответ экрана присутствия. Нужен схеме; в коде не используется."""

    date = drf_serializers.DateField()
    timezone = drf_serializers.CharField()
    counts = drf_serializers.DictField(
        child=drf_serializers.IntegerField(),
        help_text="Числа по всем найденным, даже если строк отдано меньше",
    )
    total = drf_serializers.IntegerField(help_text="Сколько нашлось всего")
    truncated = drf_serializers.BooleanField(
        help_text=(
            f"В «items» попали не все: их больше {PRESENCE_MAX_ROWS}. "
            "Сузьте область офисом, регионом или состоянием."
        ),
    )
    items = PresenceRowSerializer(many=True)


class PageSerializer(drf_serializers.Serializer):
    next_cursor = drf_serializers.CharField(allow_null=True)
    has_more = drf_serializers.BooleanField()


class EventPageSerializer(PageSerializer):
    items = AttendanceEventSerializer(many=True)


class SessionPageSerializer(PageSerializer):
    items = AttendanceSessionSerializer(many=True)


class CorrectionPageSerializer(PageSerializer):
    items = CorrectionRequestSerializer(many=True)


def _uuid_param(request, name: str) -> uuid.UUID | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть UUID", details={"field": name, "value": raw}
        ) from exc


def _date_param(request, name: str) -> date | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError) as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть датой в формате ГГГГ-ММ-ДД",
            details={"field": name, "value": raw},
        ) from exc


def _flag(request, name: str) -> bool:
    return (request.query_params.get(name) or "").lower() in ("1", "true", "yes")


class AttendanceViewSet(ServiceViewSet):
    """Списки событий и сессий."""

    service_class = AttendanceHrService

    def _common(self, request) -> dict:
        return {
            "employee_id": _uuid_param(request, "employee_id"),
            "office_id": _uuid_param(request, "office_id"),
            "region_id": _uuid_param(request, "region_id"),
            "date_from": _date_param(request, "date_from"),
            "date_to": _date_param(request, "date_to"),
        }

    @extend_schema(
        summary="Журнал сканирований",
        description=(
            "Сырые события отметки, включая отклонённые. Только чтение: "
            "строка события не меняется никогда, а исправление — это "
            "решение по заявке или новое событие с source = MANUAL."
        ),
        parameters=SCOPE_PARAMS
        + [
            OpenApiParameter("event_type", str, enum=["ENTRY", "EXIT"]),
            OpenApiParameter("source", str, enum=["QR", "MANUAL", "IMPORT"]),
            OpenApiParameter(
                "verification_status", str,
                enum=["ACCEPTED", "REJECTED", "REVIEW"],
            ),
        ],
        responses=EventPageSerializer,
        tags=["Посещаемость"],
    )
    def events(self, request):
        params = self._common(request)
        page = self.service.events(
            self.actor,
            **params,
            event_type=request.query_params.get("event_type") or None,
            source=request.query_params.get("source") or None,
            verification_status=(
                request.query_params.get("verification_status") or None
            ),
            **self._paging(request),
        )
        # Авторы ручных отметок — одним запросом на страницу, а не по
        # запросу на строку.
        user_ids = {
            (row.event_metadata or {}).get("created_by_user_id")
            for row in page.items
            if row.source == "MANUAL"
        } - {None}
        authors = {}
        if user_ids:
            from humotech.accounts.models import User

            for user in User.objects.select_related("employee").filter(id__in=user_ids):
                name = (
                    " ".join(one for one in [user.employee.last_name, user.employee.first_name] if one)
                    if user.employee_id else ""
                )
                authors[str(user.id)] = name or (user.full_name or "").strip() or user.email
        data = AttendanceEventSerializer(
            page.items, many=True, context={"authors": authors},
        ).data
        return Response(
            {"items": data, "next_cursor": page.next_cursor, "has_more": page.has_more}
        )

    @extend_schema(
        summary="Рабочие сессии",
        parameters=SCOPE_PARAMS
        + [
            OpenApiParameter(
                "status", str,
                enum=["OPEN", "CLOSED", "CORRECTED", "INVALID"],
            ),
            OpenApiParameter(
                "open", bool,
                description="Только незакрытые сессии, без времени выхода",
            ),
        ],
        responses=SessionPageSerializer,
        tags=["Посещаемость"],
    )
    def sessions(self, request):
        params = self._common(request)
        page = self.service.sessions(
            self.actor,
            **params,
            status=request.query_params.get("status") or None,
            only_open=_flag(request, "open"),
            **self._paging(request),
        )
        return self.page_response(page, serializer_class=AttendanceSessionSerializer)

    def _paging(self, request) -> dict:
        paging: dict = {"cursor": request.query_params.get("cursor") or None}
        limit = request.query_params.get("limit")
        if limit is not None:
            paging["limit"] = self._positive_int(limit, "limit")
        return paging


class PresenceView(APIView):
    """Кто где на выбранный день.

    Ответ отдаётся целиком, без страниц: по нему считаются карточки
    дашборда, а итог по первым пятидесяти строкам — это не итог. Размер
    ограничивает область видимости, а не параметр от клиента.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Кто где на выбранный день",
        description=(
            "Состав смены целиком, без страниц: по этому ответу считаются "
            "карточки дашборда, а итог по первым пятидесяти строкам — "
            "не итог. Поле counts содержит те же числа, что и карточки."
        ),
        parameters=[
            OpenApiParameter("date", str, description="День, ГГГГ-ММ-ДД"),
            OpenApiParameter("office_id", str),
            OpenApiParameter("region_id", str),
            OpenApiParameter("department_id", str),
            OpenApiParameter("position_id", str),
            OpenApiParameter("schedule_id", str),
            OpenApiParameter(
                "state", str, enum=list(PRESENCE_STATES),
                description="Оставить только одно состояние",
            ),
            OpenApiParameter("search", str, description="Поиск по ФИО и номеру"),
        ],
        responses=PresenceResponseSerializer,
        tags=["Посещаемость"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        report = AttendanceHrService().presence(
            actor,
            day=_date_param(request, "date"),
            office_id=_uuid_param(request, "office_id"),
            region_id=_uuid_param(request, "region_id"),
            department_id=_uuid_param(request, "department_id"),
            position_id=_uuid_param(request, "position_id"),
            schedule_id=_uuid_param(request, "schedule_id"),
            state=request.query_params.get("state") or None,
            search=request.query_params.get("search") or None,
        )
        return Response(
            {
                "date": report.day.isoformat(),
                "timezone": report.timezone,
                # Сводка рядом со строками: дашборд берёт числа отсюда,
                # а не пересчитывает их у себя. Один источник — одна правда.
                # Считается по всему набору, даже когда строк отдано меньше.
                "counts": report.counts(),
                "total": len(report.rows),
                "truncated": len(report.rows) > PRESENCE_MAX_ROWS,
                "items": PresenceRowSerializer(
                    report.rows[:PRESENCE_MAX_ROWS], many=True
                ).data,
            }
        )


class CorrectionListView(APIView):
    """Заявки на исправление отметок."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Заявки на исправление отметок",
        parameters=[
            OpenApiParameter(
                "status", str,
                enum=["DRAFT", "SUBMITTED", "IN_REVIEW", "APPROVED",
                      "REJECTED", "CANCELLED"],
            ),
            OpenApiParameter("employee_id", str),
            OpenApiParameter("office_id", str),
            OpenApiParameter("region_id", str),
            OpenApiParameter("cursor", str),
        ],
        responses=CorrectionPageSerializer,
        tags=["Посещаемость"],
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        page = AttendanceHrService().corrections(
            actor,
            status=request.query_params.get("status") or None,
            employee_id=_uuid_param(request, "employee_id"),
            office_id=_uuid_param(request, "office_id"),
            region_id=_uuid_param(request, "region_id"),
            cursor=request.query_params.get("cursor") or None,
        )
        return Response(
            {
                "items": CorrectionRequestSerializer(page.items, many=True).data,
                "next_cursor": page.next_cursor,
                "has_more": page.has_more,
            }
        )


class CorrectionDecisionView(APIView):
    """Одобрить или отклонить заявку.

    Решение в пути, а не в теле: две разные операции с разными правами
    на разных адресах читаются в журнале доступа без разбора тела запроса.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Решение по заявке на исправление",
        description=(
            "decision — approve или reject. Событие при этом не "
            "переписывается: меняется расчётная сессия, а решение остаётся "
            "в журнале с автором, временем и причиной."
        ),
        request=CorrectionDecisionSerializer,
        responses=CorrectionRequestSerializer,
        tags=["Посещаемость"],
    )
    def post(self, request, request_id, decision):
        actor = Actor.from_user(request.user)
        payload = validated(CorrectionDecisionSerializer, request.data)
        result = AttendanceHrService().review_correction(
            actor,
            request_id,
            decision=decision,
            comment=payload.get("comment"),
        )
        return Response(CorrectionRequestSerializer(result).data)


class ManualEventView(APIView):
    """Ручная отметка кадровика.

    Это добавление события, а не правка существующего: `source = MANUAL`
    отличает её навсегда, причина обязательна.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Ручная отметка кадровика",
        description=(
            "Добавляет НОВОЕ событие с source = MANUAL. Причина "
            "обязательна: по этим отметкам считают рабочее время."
        ),
        request=ManualEventSerializer,
        responses=AttendanceEventSerializer,
        tags=["Посещаемость"],
    )
    def post(self, request):
        actor = Actor.from_user(request.user)
        payload = validated(ManualEventSerializer, request.data)
        event = AttendanceHrService().manual_event(actor, **payload)
        return Response(
            AttendanceEventSerializer(event).data,
            status=http_status.HTTP_201_CREATED,
        )


class DailyRowSerializer(drf_serializers.Serializer):
    """Один день журнала. Все величины посчитал сервер."""

    day = drf_serializers.DateField()
    timezone = drf_serializers.CharField(
        help_text="Пояс ОФИСА, по которому определён этот день",
    )
    office_id = drf_serializers.UUIDField(allow_null=True)
    office_name = drf_serializers.CharField(allow_null=True)
    state = drf_serializers.CharField(
        help_text=(
            "IN_OFFICE, LEFT, NOT_COME, DAY_OFF, NO_SCHEDULE, SICK_LEAVE, "
            "VACATION, OTHER_ABSENCE. «Нет графика» и «выходной» — разные "
            "состояния, и оба не равны прогулу"
        ),
    )
    first_entry_at = drf_serializers.DateTimeField(allow_null=True)
    last_exit_at = drf_serializers.DateTimeField(allow_null=True)
    seconds = drf_serializers.IntegerField(
        help_text=(
            "Сумма учитываемых интервалов, а не разница первого входа и "
            "последнего выхода: перерывы между сессиями сюда не входят"
        ),
    )
    sessions = drf_serializers.IntegerField()
    open_session_id = drf_serializers.UUIDField(
        allow_null=True,
        help_text="Незакрытая сессия. Её длительность считает сервер",
    )
    late_minutes = drf_serializers.IntegerField(
        allow_null=True,
        help_text=(
            "Минуты СВЕРХ допуска графика. `null` — сравнивать не с чем: "
            "нет графика или день нерабочий"
        ),
    )
    scheduled_start = drf_serializers.TimeField(allow_null=True)
    absence_code = drf_serializers.CharField(allow_null=True)
    absence_name = drf_serializers.CharField(allow_null=True)
    conflicting_marks = drf_serializers.BooleanField(
        help_text="Отметки в день подтверждённого отсутствия — расхождение",
    )


class DailyTotalsSerializer(drf_serializers.Serializer):
    """Итоги за ВЕСЬ период, а не за показанные строки."""

    seconds = drf_serializers.IntegerField()
    days_with_marks = drf_serializers.IntegerField()
    working_days = drf_serializers.IntegerField()
    late_days = drf_serializers.IntegerField()
    late_minutes = drf_serializers.IntegerField()
    open_sessions = drf_serializers.IntegerField()


class DailyResponseSerializer(drf_serializers.Serializer):
    employee_id = drf_serializers.UUIDField()
    timezone = drf_serializers.CharField()
    first = drf_serializers.DateField()
    last = drf_serializers.DateField()
    days = DailyRowSerializer(many=True)
    totals = DailyTotalsSerializer()
    note = drf_serializers.CharField(allow_null=True)


@extend_schema(tags=["Посещаемость"])
class EmployeeDailyView(APIView):
    """Журнал по дням для одного сотрудника. Требует `attendance.read`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="attendance_daily",
        summary="Посещаемость сотрудника по дням",
        description=(
            "Строку дня собирает тот же код, что и присутствие на "
            "дашборде: у ночной смены, открытой сессии и допуска "
            "опоздания один ответ, а не два похожих.\n\n"
            "Три правила, которые из-за этого достаются журналу даром. "
            "День определяется поясом ОФИСА, а не браузера: перевод в "
            "офис с другим поясом меняет пояс со дня перевода. Ночная "
            "смена принадлежит дню, в который НАЧАЛАСЬ. Открытая сессия "
            "учитывается до момента серверного расчёта — клиент, "
            "вычитающий «сейчас минус вход», получил бы другое число.\n\n"
            "Итоги считаются по всему периоду, а не по показанным "
            "строкам: сводка, зависящая от длины таблицы, отвечает "
            "не на тот вопрос."
        ),
        parameters=[
            OpenApiParameter(
                "employee_id", OpenApiTypes.UUID, required=True,
                description="Чужой сотрудник отвечает «не найден»",
            ),
            OpenApiParameter("date_from", str, required=True),
            OpenApiParameter("date_to", str, required=True),
        ],
        responses=DailyResponseSerializer,
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        employee_id = _uuid_param(request, "employee_id")
        if employee_id is None:
            raise ValidationFailed(
                "Параметр «employee_id» обязателен",
                details={"field": "employee_id"},
            )
        first = _date_param(request, "date_from")
        last = _date_param(request, "date_to")
        if first is None or last is None:
            raise ValidationFailed(
                "Период обязателен: передайте date_from и date_to",
                details={"fields": ["date_from", "date_to"]},
            )
        return Response(
            AttendanceHrService().daily(
                actor, employee_id, first=first, last=last
            )
        )
