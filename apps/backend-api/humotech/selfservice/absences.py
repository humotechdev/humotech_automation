"""Экраны больничных и отпусков в личном кабинете.

Больничный и отпуск — одна механика с разными видами отсутствия, поэтому
и endpoint'ы общие: `POST /me/absences` с кодом вида. Разводить их на два
набора значило бы иметь два места, где чинить одну ошибку.

Различие только в том, что показывать: у отпуска есть остаток, у
больничного — справка. Это решает клиент по коду вида и по политике
организации, которую сервер отдаёт вместе со списком видов.
"""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from django.http import HttpResponse
from rest_framework.response import Response

from humotech.absences.services import MINUTES_PER_WORKING_DAY, AbsenceService
from humotech.core.api import validated
from humotech.core.errors import ValidationFailed
from humotech.selfservice.views import EmployeeSelfView


class AbsenceCreateSerializer(serializers.Serializer):
    """Заявка. `employee_id` здесь нет и быть не может.

    Даты необязательны: заболевший не знает, когда выйдет, и требовать
    от него число значит получить выдуманное. Что делать с пустым
    периодом, решает сервис — у отпуска он его не примет.
    """

    absence_type_code = serializers.CharField(max_length=50)
    first_day = serializers.DateField(required=False, allow_null=True)
    last_day = serializers.DateField(required=False, allow_null=True)
    comment = serializers.CharField(
        max_length=2000, required=False, allow_blank=True
    )


class ExtendSerializer(serializers.Serializer):
    new_last_day = serializers.DateField()
    comment = serializers.CharField(
        max_length=2000, required=False, allow_blank=True
    )


class AbsenceTypeSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    requires_document = serializers.BooleanField()
    deducts_leave_balance = serializers.BooleanField()


class AbsenceDocumentSerializer(serializers.Serializer):
    """Приложенный файл. Ссылки на скачивание здесь нет намеренно:
    справка отдаётся отдельным запросом, который спрашивает, кому можно."""

    id = serializers.UUIDField()
    file_name = serializers.CharField()
    uploaded_at = serializers.DateTimeField()


class AbsenceRequestSerializer(serializers.Serializer):
    """Заявка глазами самого сотрудника."""

    id = serializers.UUIDField()
    kind = serializers.CharField()
    absence_type = AbsenceTypeSerializer()
    status = serializers.CharField()
    stage = serializers.CharField()
    certificate_status = serializers.CharField(allow_null=True)
    certificate_comment = serializers.CharField(allow_null=True)
    extension_pending = serializers.BooleanField(
        help_text=(
            "Производное состояние, которого нет в схеме отдельным "
            "статусом: продление — это дочерняя заявка, а не поле "
            "у родительской"
        ),
    )
    first_day = serializers.DateField(allow_null=True)
    last_day = serializers.DateField(allow_null=True)
    working_days = serializers.IntegerField(allow_null=True)
    comment = serializers.CharField(allow_null=True)
    review_comment = serializers.CharField(allow_null=True)
    documents = serializers.ListField(child=serializers.JSONField())
    can_cancel = serializers.BooleanField()
    submitted_at = serializers.DateTimeField(allow_null=True)
    reviewed_at = serializers.DateTimeField(allow_null=True)
    absence_status = serializers.CharField(allow_null=True)


class AbsenceRequestListSerializer(serializers.Serializer):
    requests = AbsenceRequestSerializer(many=True)
    total = serializers.IntegerField()
    offset = serializers.IntegerField()
    limit = serializers.IntegerField()
    has_more = serializers.BooleanField()


class AbsenceOptionTypeSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    requires_document = serializers.BooleanField()
    document_required_after_days = serializers.IntegerField(allow_null=True)
    deducts_leave_balance = serializers.BooleanField()
    is_paid = serializers.BooleanField()


class AbsencePolicySerializer(serializers.Serializer):
    """Правила организации — подсказка интерфейсу, а не разрешение.

    Решает всё равно сервер: клиент по этим полям показывает верные
    подсказки и не предлагает того, чего организация не разрешает.
    """

    require_hr_approval = serializers.BooleanField()
    document_required = serializers.BooleanField()
    document_can_be_added_later = serializers.BooleanField()
    employee_may_cancel_pending = serializers.BooleanField()
    cancelling_approved_requires_hr = serializers.BooleanField()
    extensions_allowed = serializers.BooleanField()
    max_document_bytes = serializers.IntegerField()
    allowed_document_types = serializers.ListField(
        child=serializers.CharField()
    )
    allow_negative_leave_balance = serializers.BooleanField()
    document_required_from_day = serializers.IntegerField(allow_null=True)
    backdating_days_allowed = serializers.IntegerField(allow_null=True)
    vacation_min_days_ahead = serializers.IntegerField(allow_null=True)


class AbsenceOptionsSerializer(serializers.Serializer):
    types = AbsenceOptionTypeSerializer(many=True)
    policy = AbsencePolicySerializer()


class LeaveBalanceRowSerializer(serializers.Serializer):
    absence_type = serializers.JSONField()
    year = serializers.IntegerField()
    allocated_days = serializers.FloatField()
    used_days = serializers.FloatField()
    reserved_days = serializers.FloatField(
        help_text=(
            "Отложено под заявки, которые ещё не рассмотрены. Показано "
            "отдельно: человек должен понимать, почему доступного меньше, "
            "чем начисленного минус использованное"
        ),
    )
    available_days = serializers.FloatField()


class LeaveBalanceSerializer(serializers.Serializer):
    balances = LeaveBalanceRowSerializer(many=True)


class DocumentUploadSerializer(serializers.Serializer):
    document = serializers.FileField()


def request_json(view) -> dict:
    request = view.request
    absence = view.absence
    return {
        "id": str(request.id),
        "kind": request.request_kind,
        "absence_type": {
            "code": request.absence_type.code,
            "name": request.absence_type.name,
            "requires_document": request.absence_type.requires_document,
            "deducts_leave_balance": request.absence_type.deducts_leave_balance,
        },
        "status": request.status,
        # Состояние словами человека: «ожидаем документы», «на проверке
        # HR», «нужны исправления». Считается на сервере и приходит
        # готовым — иначе каждый клиент собирал бы его по-своему и
        # однажды назвал бы неподтверждённый больничный подтверждённым.
        "stage": view.stage,
        # Судьба справки: PENDING, VERIFIED, REJECTED или null. По ней
        # приложение решает, показывать ли «Прикрепить справку»: после
        # отказа кадровика бумагу нужно принести заново, а счётчик
        # документов об этом не знает.
        "certificate_status": view.certificate_status,
        # Что кадровик сказал о справке. Уходит человеку дословно и
        # остаётся на карточке: сообщение в чате теряется в переписке
        # к следующему дню, а заявка лежит перед глазами.
        "certificate_comment": view.certificate_comment,
        # Производное состояние, которого нет в схеме отдельным статусом:
        # продление — это дочерняя заявка, а не поле у родительской.
        "extension_pending": view.extension_pending,
        "first_day": (
            request.requested_start_at.date().isoformat()
            if request.requested_start_at else None
        ),
        "last_day": (
            request.requested_end_at.date().isoformat()
            if request.requested_end_at else None
        ),
        "working_days": view.working_days,
        "comment": request.employee_comment,
        "review_comment": request.review_comment,
        "documents": view.documents,
        "can_cancel": view.can_cancel,
        "submitted_at": (
            request.submitted_at.isoformat() if request.submitted_at else None
        ),
        "reviewed_at": (
            request.reviewed_at.isoformat() if request.reviewed_at else None
        ),
        "absence_status": absence.status if absence else None,
    }


@extend_schema(tags=["Личный кабинет"])
class AbsenceListView(EmployeeSelfView):
    """Список своих заявок и подача новой."""

    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(
        operation_id="me_absences_list",
        summary="Мои заявки на отсутствие",
        parameters=[
            OpenApiParameter("limit", OpenApiTypes.INT),
            OpenApiParameter("offset", OpenApiTypes.INT),
        ],
        responses={200: AbsenceRequestListSerializer},
    )
    def get(self, request):
        service = AbsenceService()
        limit = min(max(_int_param(request, "limit", 20), 1), 100)
        # Потолок смещения: у сотрудника нет и не будет сотни тысяч заявок,
        # а огромное OFFSET — лишняя работа базы и путь к переполнению.
        offset = min(max(_int_param(request, "offset", 0), 0), 100_000)
        views, total = service.requests(self.context, limit=limit, offset=offset)
        return Response(
            {
                "requests": [request_json(view) for view in views],
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < total,
            }
        )

    @extend_schema(
        operation_id="me_absences_create",
        summary="Подать заявку",
        description=(
            "`employee_id` в теле нет и быть не может: заявка всегда "
            "подаётся за себя.\n\n"
            "Справка прикладывается тем же запросом "
            "(multipart, поле `document`), если организация её требует."
        ),
        request=AbsenceCreateSerializer,
        responses={201: AbsenceRequestSerializer},
    )
    def post(self, request):
        data = validated(AbsenceCreateSerializer, request.data)
        view = AbsenceService().create(
            self.context,
            absence_type_code=data["absence_type_code"],
            first_day=data.get("first_day"),
            last_day=data.get("last_day"),
            comment=data.get("comment") or None,
            # Справка приходит тем же запросом, если организация её требует.
            document=request.FILES.get("document"),
        )
        return Response(request_json(view), status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["Личный кабинет"],
    parameters=[
        OpenApiParameter(
            "request_id", OpenApiTypes.UUID, location=OpenApiParameter.PATH
        ),
    ],
)
class AbsenceDetailView(EmployeeSelfView):
    """Одна заявка: посмотреть, отменить."""

    @extend_schema(
        operation_id="me_absences_retrieve",
        summary="Моя заявка",
        responses={200: AbsenceRequestSerializer},
    )
    def get(self, request, request_id):
        return Response(request_json(AbsenceService().request(self.context,
                                                              request_id)))

    @extend_schema(
        operation_id="me_absences_cancel",
        summary="Отменить свою заявку",
        description=(
            "Разрешено политикой организации и только пока заявка "
            "не рассмотрена. Строка не удаляется: у заявки появляется "
            "статус «отменена»."
        ),
        responses={200: AbsenceRequestSerializer},
    )
    def delete(self, request, request_id):
        return Response(
            request_json(AbsenceService().cancel(self.context, request_id))
        )


@extend_schema(
    tags=["Личный кабинет"],
    parameters=[
        OpenApiParameter(
            "request_id", OpenApiTypes.UUID, location=OpenApiParameter.PATH
        ),
    ],
)
class AbsenceExtendView(EmployeeSelfView):
    """Продление: отдельная заявка, ссылающаяся на исходную."""

    @extend_schema(
        operation_id="me_absences_extend",
        summary="Продлить отсутствие",
        request=ExtendSerializer,
        responses={201: AbsenceRequestSerializer},
    )
    def post(self, request, request_id):
        data = validated(ExtendSerializer, request.data)
        view = AbsenceService().extend(
            self.context,
            request_id,
            new_last_day=data["new_last_day"],
            comment=data.get("comment") or None,
        )
        return Response(request_json(view), status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["Личный кабинет"],
    parameters=[
        OpenApiParameter(
            "request_id", OpenApiTypes.UUID, location=OpenApiParameter.PATH
        ),
    ],
)
class AbsenceDocumentView(EmployeeSelfView):
    """Донести справку позже — если организация это разрешает."""

    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="me_absences_attach_document",
        summary="Приложить справку",
        request=DocumentUploadSerializer,
        responses={201: AbsenceRequestSerializer},
    )
    def post(self, request, request_id):
        document = request.FILES.get("document")
        if document is None:
            return Response(
                {
                    "error": {
                        "code": "validation_failed",
                        "message": "Файл не приложен",
                        "details": {"field": "document"},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        view = AbsenceService().attach_document(self.context, request_id, document)
        return Response(request_json(view), status=status.HTTP_201_CREATED)

    @extend_schema(
        operation_id="me_absence_document",
        summary="Приложенная справка",
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    def get(self, request, request_id):
        stream, meta = AbsenceService().own_document(self.context, request_id)
        # `inline`: чаще справку просто смотрят на экране, а не
        # сохраняют — снимок из камеры открывается прямо в вебвью.
        # scan_status, удаление и безопасные заголовки — одной проверкой.
        from humotech.files.serving import EMPLOYEE, file_response

        return file_response(
            stream, meta, audience=EMPLOYEE, filename=f"certificate-{request_id}",
        )


@extend_schema(tags=["Личный кабинет"])
class AbsenceApplicationView(EmployeeSelfView):
    """Печатное заявление по заявке.

    Отдаётся файлом, а не ссылкой на хранилище: бланк собирается из
    самой заявки на каждое обращение. Сохранить его однажды значило бы
    получить бумагу, которая молча разошлась с продлённой заявкой.
    """

    @extend_schema(
        operation_id="me_absence_application",
        summary="Заявление для печати",
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    def get(self, request, request_id):
        pdf = AbsenceService().application(self.context, request_id)
        answer = HttpResponse(pdf, content_type="application/pdf")
        # `inline`, а не `attachment`: человек сперва смотрит заявление
        # на экране и печатает уже оттуда.
        answer["Content-Disposition"] = (
            f'inline; filename="application-{request_id}.pdf"'
        )
        return answer


@extend_schema(tags=["Личный кабинет"])
class AbsencePaperView(EmployeeSelfView):
    """Прислать бумагу по заявке в чат.

    Кабинет не отдаёт файл сам: вебвью Telegram не даёт сохранить его, и
    нажатие «скачать» заканчивается ничем. Бумагу присылает бот
    сообщением — оттуда её и пересылают, и печатают, и она остаётся в
    переписке.
    """

    @extend_schema(
        operation_id="me_absence_send_paper",
        summary="Прислать бумагу в чат",
        parameters=[
            OpenApiParameter(
                "request_id", OpenApiTypes.UUID, location=OpenApiParameter.PATH
            ),
            OpenApiParameter(
                "paper", OpenApiTypes.STR, location=OpenApiParameter.PATH,
                description="application — заявление, certificate — справка",
            ),
        ],
        request=None,
        responses={202: None},
    )
    def post(self, request, request_id, paper):
        if paper not in ("application", "certificate"):
            raise ValidationFailed(
                "Такой бумаги по заявке не бывает",
                details={"field": "paper"},
            )
        AbsenceService().send_paper(self.context, request_id, what=paper)
        return Response(status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=["Личный кабинет"])
class AbsenceOptionsView(EmployeeSelfView):
    """Что человек вообще может оформить и по каким правилам.

    Правила отдаются клиенту, чтобы он показывал верные подсказки и не
    предлагал того, чего организация не разрешает. Решает всё равно
    сервер — это подсказка интерфейсу, а не разрешение.
    """

    @extend_schema(
        operation_id="me_absences_options",
        summary="Что я могу оформить",
        responses={200: AbsenceOptionsSerializer},
    )
    def get(self, request):
        service = AbsenceService()
        policy = service.policy(self.context.organization_id)
        return Response(
            {
                "types": [
                    {
                        "code": row.code,
                        "name": row.name,
                        "requires_document": row.requires_document,
                        "document_required_after_days": (
                            row.document_required_after_days
                        ),
                        "deducts_leave_balance": row.deducts_leave_balance,
                        "is_paid": row.is_paid,
                    }
                    for row in service.types(self.context)
                ],
                "policy": policy.as_dict(),
            }
        )


@extend_schema(tags=["Личный кабинет"])
class LeaveBalanceView(EmployeeSelfView):
    """Остаток отпуска.

    Минуты переводятся в дни здесь, а не на клиенте: делить на 480 в трёх
    приложениях — три места, где ошибиться.
    """

    @extend_schema(
        operation_id="me_leave_balance",
        summary="Мой остаток отпуска",
        parameters=[OpenApiParameter("year", OpenApiTypes.INT)],
        responses={200: LeaveBalanceSerializer},
    )
    def get(self, request):
        year = request.query_params.get("year")
        balances = AbsenceService().balances(
            self.context, year=int(year) if year and year.isdigit() else None
        )
        return Response(
            {
                "balances": [
                    {
                        "absence_type": {
                            "code": row.absence_type.code,
                            "name": row.absence_type.name,
                        },
                        "year": row.year,
                        "allocated_days": _days(row.allocated_minutes),
                        "used_days": _days(row.used_minutes),
                        # Отложено под заявки, которые ещё не рассмотрены.
                        # Показывается отдельно: человек должен понимать,
                        # почему доступного меньше, чем начисленного минус
                        # использованное.
                        "reserved_days": _days(row.reserved_minutes),
                        "available_days": _days(row.available_minutes),
                    }
                    for row in balances
                ]
            }
        )


def _int_param(request, name: str, default: int) -> int:
    """Целое из строки запроса. Мусор — 400, а не 500 из `int()`."""
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationFailed(
            "Ожидается целое число", details={"field": name}
        ) from None


def _days(minutes: int) -> float:
    return round(minutes / MINUTES_PER_WORKING_DAY, 2)


__all__ = [
    "AbsenceDetailView",
    "AbsenceDocumentView",
    "AbsenceExtendView",
    "AbsenceListView",
    "AbsenceOptionsView",
    "LeaveBalanceView",
]
