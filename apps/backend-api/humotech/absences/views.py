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

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.absences.services import (
    AbsenceService,
    approval_blockers,
    stage_of,
)
from humotech.core.api import validated
from humotech.core.timeframes import local_date, organization_zone
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor


class DecisionSerializer(serializers.Serializer):
    comment = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
        help_text="Основание решения. Уходит сотруднику в чат",
    )
    override_marks = serializers.BooleanField(
        required=False, default=False,
        help_text=(
            "Утвердить больничный, хотя в его дни есть отметки входа и "
            "выхода. Без этого флага сервер отказывает: молча списать "
            "отработанный день в больничный нельзя. Требует причины"
        ),
    )


class AbsenceEmployeeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    employee_number = serializers.CharField(allow_null=True)


class AbsenceTypeBriefSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()


class LeaveCheckSerializer(serializers.Serializer):
    """Остаток отпуска против периода заявки.

    `available_days` пустой — остаток на этот год не начислен: сравнивать
    не с чем, и это не то же самое, что «не хватает».
    """

    year = serializers.IntegerField()
    needed_days = serializers.IntegerField(help_text="Рабочих дней в периоде")
    available_days = serializers.IntegerField(allow_null=True)
    enough = serializers.BooleanField()


class OverlapSerializer(serializers.Serializer):
    """Отсутствие того же человека, задевающее эти дни."""

    kind = serializers.CharField(help_text="absence — подтверждённое, request — заявка")
    absence_type_name = serializers.CharField()
    first_day = serializers.DateField()
    last_day = serializers.DateField()


class HrAbsenceRequestSerializer(serializers.Serializer):
    """Заявка глазами кадровика.

    Комментарий сотрудника здесь есть: тот, кто принимает решение,
    должен видеть, о чём его просят. Диагноза в нём быть не должно, и
    подсказка об этом стоит в самом поле ввода в приложении.
    """

    id = serializers.UUIDField()
    employee = AbsenceEmployeeSerializer()
    kind = serializers.CharField()
    parent_request_id = serializers.UUIDField(
        allow_null=True, help_text="Заявка на продление ссылается на исходную",
    )
    absence_type = AbsenceTypeBriefSerializer()
    application_received_at = serializers.DateTimeField(
        allow_null=True, required=False,
        help_text=(
            "Подписанное заявление пришло по почте. Второй, независимый "
            "от справки пункт проверки"
        ),
    )
    status = serializers.CharField()
    stage = serializers.CharField(
        help_text=(
            "Состояние словами человека — то же, что видит сотрудник: "
            "WAITING_DOCUMENTS, HR_REVIEW, NEEDS_FIX, PENDING, APPROVED, "
            "REJECTED, CANCELLED"
        ),
    )
    missing_for_approval = serializers.ListField(
        child=serializers.CharField(),
        help_text=(
            "Чего не хватает до подтверждения, по порядку работы "
            "кадровика: certificate, application, period. Пустой список "
            "значит «можно подтверждать»"
        ),
    )
    first_day = serializers.DateField(allow_null=True)
    last_day = serializers.DateField(allow_null=True)
    comment = serializers.CharField(allow_null=True)
    review_comment = serializers.CharField(allow_null=True)
    submitted_at = serializers.DateTimeField(allow_null=True)
    # Показания, которые считаются запросами: их отдаёт ответ по одной
    # заявке, где принимают решение, и не отдаёт очередь.
    leave_balance = LeaveCheckSerializer(
        allow_null=True, required=False,
        help_text="Остаток отпуска против периода заявки. Пусто у больничного",
    )
    overlap = OverlapSerializer(
        allow_null=True, required=False,
        help_text="Чужое отсутствие на те же дни, если оно есть",
    )
    history = serializers.ListField(
        child=serializers.DictField(),
        help_text=(
            "Шаги заявки по порядку: at, action, comment. Комментарий есть "
            "только у решений кадровика"
        ),
    )


class PendingAbsenceRequestsSerializer(serializers.Serializer):
    requests = HrAbsenceRequestSerializer(many=True)


def hr_request_json(request, tz=None, *, deep: bool = False) -> dict:
    """Заявка глазами кадровика.

    Комментарий сотрудника здесь есть: тот, кто принимает решение, должен
    видеть, о чём его просят. Диагноза в нём быть не должно, и подсказка
    об этом стоит в самом поле ввода в приложении.

    `tz` — пояс показа. Передаётся списком сразу на всю выдачу: считать
    его на каждую строку значило бы лишний запрос на заявку.

    `deep` — добавить показания, которые считаются запросами: остаток
    отпуска и пересечение периодов. Нужны на странице одной заявки, где
    принимают решение; в очереди их не показывают, и платить за них
    обходом графиков на каждую строку незачем.
    """
    zone = tz or organization_zone(request.organization_id)
    extra: dict = {}
    if deep:
        service = AbsenceService()
        clash = service.overlap_check(request)
        extra = {
            "leave_balance": service.leave_check(request),
            "overlap": {
                "kind": clash.kind,
                "absence_type_name": clash.absence_type_name,
                "first_day": clash.first_day.isoformat(),
                "last_day": clash.last_day.isoformat(),
            } if clash else None,
        }
    return {
        **extra,
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
        # Та же стадия, что видит сотрудник: кадровик и человек должны
        # называть состояние заявки одинаково, иначе разговор начинается
        # с выяснения, кто что имел в виду.
        "stage": stage_of(request),
        # Чего не хватает до подтверждения — списком кодов, в том
        # порядке, в каком это делает кадровик: справка, заявление,
        # даты. Пустой список значит «можно подтверждать».
        "missing_for_approval": list(approval_blockers(request)),
        # Числа периода — в поясе организации. В UTC отпуск,
        # начинающийся пятого числа в полночь по Ташкенту, приходился на
        # вечер четвёртого: кадровик видел период на день длиннее и
        # начинающийся не тогда, когда его просили.
        "first_day": (
            local_date(request.requested_start_at, zone).isoformat()
            if request.requested_start_at else None
        ),
        "last_day": (
            local_date(request.requested_end_at, zone).isoformat()
            if request.requested_end_at else None
        ),
        # Подписанное заявление пришло по почте. Второй, независимый от
        # справки пункт: бумага подтверждает намерение человека, справка
        # — факт болезни, и приходят они разными дорогами.
        "application_received_at": (
            request.application_received_at.isoformat()
            if request.application_received_at else None
        ),
        # Документы отдельно от самой заявки: «справка загружена» и
        # «справка проверена» — разные состояния, и слить их значило бы
        # считать документ проверенным по факту загрузки.
        "documents": [
            {
                "id": str(document.id),
                "document_type": document.document_type,
                "verification_status": document.verification_status,
                "verified_at": (
                    document.verified_at.isoformat() if document.verified_at else None
                ),
                # Что кадровик сказал о бумаге. Без этого «нужна новая
                # версия» на странице заявки означает «что-то не так,
                # догадайся сам» — и следующий раз приносят то же самое.
                "verification_comment": document.verification_comment,
                "file": {
                    "id": str(document.file_id),
                    "name": document.file.original_filename,
                    "mime_type": document.file.mime_type,
                    "size_bytes": document.file.size_bytes,
                    "uploaded_at": document.file.created_at.isoformat(),
                    "scan_status": document.file.scan_status,
                },
            }
            for document in request.documents.all()
        ],
        "requires_document": request.absence_type.requires_document,
        "comment": request.employee_comment,
        "review_comment": request.review_comment,
        # Когда по заявке приняли решение. Отдельно от `submitted_at`:
        # заявку подают и решают в разные дни, и «последние решения»
        # строятся именно по второй дате.
        "reviewed_at": (
            request.reviewed_at.isoformat() if request.reviewed_at else None
        ),
        "submitted_at": (
            request.submitted_at.isoformat() if request.submitted_at else None
        ),
        # История — неизменяемые записи `AbsenceAction` по порядку. Комментарий
        # отдаётся только у решений кадровика: у шагов сотрудника в нём
        # бывает диагноз, а история видна всем, кто разбирает очередь.
        "history": [
            {
                "at": action.created_at.isoformat(),
                "action": action.action,
                # Кто это сделал. Без имени история отвечает «что
                # произошло», но не «с кого спрашивать», а спор о
                # больничном — это всегда спор о чьём-то решении.
                "actor": _actor_name(action),
                "comment": (
                    action.comment
                    if action.action in HR_DECISION_ACTIONS
                    else None
                ),
            }
            for action in sorted(request.actions.all(), key=lambda one: one.created_at)
        ],
    }


def _actor_name(action) -> str | None:
    """Имя того, кто сделал шаг. `None` — шаг сделал сам заявитель.

    Шаги сотрудника остаются без подписи намеренно: это его заявка, его
    имя стоит наверху страницы, и повторять его у каждой строки значит
    заглушить те строки, где имя как раз важно, — решения кадровика.
    """
    if action.actor_user_id and action.actor_user:
        return action.actor_user.full_name or action.actor_user.email
    return None


# Шаги, комментарий к которым пишет кадровик, а не сотрудник.
#
# Отказ по справке сюда входит: причина написана кадровиком и уходит
# человеку дословно, а в истории она — главное. Комментарий сотрудника
# по-прежнему скрыт: в нём бывает диагноз.
HR_DECISION_ACTIONS = (
    "APPROVED", "REJECTED", "CANCELLED",
    "DOCUMENT_REJECTED", "PERIOD_SET", "MARKS_OVERRIDDEN",
)


@extend_schema(tags=["Отсутствия"])
class PendingAbsenceRequestsView(APIView):
    """Очередь заявок, ожидающих решения."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="absence_requests_pending",
        summary="Заявки, ожидающие решения",
        description=(
            "Только заявки сотрудников из области видимости кадровика. "
            "Постраничного вывода здесь нет намеренно: очередь на решение "
            "не бывает длинной, а если стала — это повод разобраться, "
            "а не листать."
        ),
        responses={200: PendingAbsenceRequestsSerializer},
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        rows = AbsenceService().pending(actor)
        zone = organization_zone(actor.organization_id)
        return Response({"requests": [hr_request_json(row, zone) for row in rows]})


@extend_schema(tags=["Отсутствия"])
class AbsenceRequestView(APIView):
    """Одна заявка целиком: бумаги, история, чего не хватает до решения.

    Тот же состав, что в очереди, — намеренно: страница заявки и строка
    очереди обязаны говорить об одном и том же одними словами.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="absence_request_read",
        summary="Заявка на отсутствие",
        parameters=[
            OpenApiParameter(
                "request_id", OpenApiTypes.UUID, location=OpenApiParameter.PATH
            ),
        ],
        responses={200: HrAbsenceRequestSerializer},
    )
    def get(self, request, request_id):
        row = AbsenceService().for_hr(Actor.from_user(request.user), request_id)
        return Response(hr_request_json(row, deep=True))


@extend_schema(tags=["Отсутствия"])
class AbsenceDecisionView(APIView):
    """Подтвердить, отклонить или отменить подтверждённое.

    Отмена подтверждённого — отдельное действие, а не «отклонить ещё раз»:
    отсутствие уже существует и попало в статистику, и снимать его нужно
    вместе с ним.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="absence_request_decide",
        summary="Решение по заявке",
        parameters=[
            OpenApiParameter(
                "request_id", OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
            ),
            OpenApiParameter(
                "decision", str, location=OpenApiParameter.PATH,
                enum=["approve", "reject", "cancel"],
                description=(
                    "cancel — снять уже подтверждённое отсутствие вместе "
                    "с самим отсутствием, а не отклонить заявку"
                ),
            ),
        ],
        request=DecisionSerializer,
        responses={200: HrAbsenceRequestSerializer},
    )
    def post(self, request, request_id, decision):
        # Слово решения — из закрытого списка. Раньше всё, что не
        # `cancel`, уходило в `decide(approve=decision == "approve")`, и
        # опечатка в адресе («APPROVE», «approved») молча ОТКЛОНЯЛА заявку.
        if decision not in ("approve", "reject", "cancel"):
            raise ValidationFailed(
                "Решение может быть только approve, reject или cancel",
                details={"decision": decision[:50]},
            )
        actor = Actor.from_user(request.user)
        data = validated(DecisionSerializer, request.data)
        comment = data.get("comment") or None
        service = AbsenceService()

        if decision == "cancel":
            row = service.cancel_approved(actor, request_id, comment=comment)
        else:
            row = service.decide(
                actor, request_id, approve=decision == "approve",
                comment=comment, override_marks=bool(data.get("override_marks"))
            )
        return Response(hr_request_json(row, deep=True))


class PeriodSerializer(serializers.Serializer):
    """Фактические даты по справке."""

    first_day = serializers.DateField()
    last_day = serializers.DateField()
    comment = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=1000
    )


@extend_schema(tags=["Отсутствия"])
class AbsencePeriodView(APIView):
    """Проставить фактические даты больничного по справке.

    Ради этого даты в заявке и сделаны необязательными: человек подал
    её, не зная, когда выйдет, а в справке стоит точный период.
    Кадровик переносит его в заявку — и только после этого её можно
    подтвердить.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="absence_request_set_period",
        summary="Фактические даты по справке",
        parameters=[
            OpenApiParameter(
                "request_id", OpenApiTypes.UUID, location=OpenApiParameter.PATH
            ),
        ],
        request=PeriodSerializer,
        responses={200: HrAbsenceRequestSerializer},
    )
    def post(self, request, request_id):
        data = validated(PeriodSerializer, request.data)
        row = AbsenceService().set_period(
            Actor.from_user(request.user),
            request_id,
            first_day=data["first_day"],
            last_day=data["last_day"],
            comment=data.get("comment") or None,
        )
        return Response(hr_request_json(row, deep=True))


@extend_schema(tags=["Отсутствия"])
class AbsenceApplicationReceivedView(APIView):
    """Отметить, что подписанное заявление дошло по почте.

    Отдельно от решения по справке: два независимых пункта проверки, и
    одна отметка на оба означала бы, что половину работы кадровик
    подтверждает не глядя.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="absence_request_application_received",
        summary="Заявление получено",
        parameters=[
            OpenApiParameter(
                "request_id", OpenApiTypes.UUID, location=OpenApiParameter.PATH
            ),
            OpenApiParameter(
                "received", OpenApiTypes.BOOL,
                description="false — снять отметку, поставленную по ошибке",
            ),
        ],
        request=None,
        responses={200: HrAbsenceRequestSerializer},
    )
    def post(self, request, request_id):
        received = str(request.query_params.get("received", "true")).lower()
        row = AbsenceService().mark_application_received(
            Actor.from_user(request.user),
            request_id,
            received=received not in ("false", "0"),
        )
        return Response(hr_request_json(row, deep=True))


@extend_schema(tags=["Отсутствия"])
class AbsenceDocumentDownloadView(APIView):
    """Файл справки к заявке.

    Отдаётся отсюда, а не веб-сервером: файл лежит в приватном хранилище,
    и право на него спрашивается при каждом открытии. `inline` — PDF и
    картинку браузер показывает сам, сохранить их можно из просмотрщика.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="absence_request_document_download",
        summary="Файл справки к заявке",
        parameters=[
            OpenApiParameter("request_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
            OpenApiParameter("document_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        ],
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    def get(self, request, request_id, document_id):
        actor = Actor.from_user(request.user)
        stream, record = AbsenceService().open_document(actor, request_id, document_id)
        # scan_status, удаление и безопасные заголовки — одной проверкой.
        from humotech.files.serving import STAFF, file_response

        return file_response(stream, record, audience=STAFF)


class DocumentDecisionSerializer(serializers.Serializer):
    """Решение по справке.

    Причина обязательна при отказе и проверяется сервисом: отклонение
    без объяснения — тупик, человек приносит ту же бумагу второй раз.
    """

    comment = serializers.CharField(required=False, allow_blank=True,
                                    allow_null=True, max_length=1000)


class AbsenceDocumentDecisionView(APIView):
    """Принять справку или отклонить её с причиной.

    Решение по бумаге не меняет решения по заявке: одобренный больничный
    с отклонённой справкой — законное состояние, HR ждёт правильный
    документ, а человек всё это время болеет.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="absence_request_document_decision",
        summary="Принять или отклонить справку",
        parameters=[
            OpenApiParameter("request_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
            OpenApiParameter("document_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
            OpenApiParameter("decision", OpenApiTypes.STR, OpenApiParameter.PATH,
                             enum=["accept", "reject"]),
        ],
        request=DocumentDecisionSerializer,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request, request_id, document_id, decision):
        if decision not in ("accept", "reject"):
            raise ValidationFailed(
                "Решение может быть только accept или reject",
                details={"decision": decision},
            )
        payload = validated(DocumentDecisionSerializer, request.data)
        actor = Actor.from_user(request.user)
        document = AbsenceService().verify_document(
            actor, request_id, document_id,
            accept=decision == "accept",
            comment=payload.get("comment"),
        )
        return Response({
            "id": str(document.id),
            "verification_status": document.verification_status,
            "verification_comment": document.verification_comment,
        })


class AbsenceApplicationView(APIView):
    """Печатное заявление по заявке — для кадровика.

    Тот же бланк, что видит сотрудник. Нужен, когда человек принёс не ту
    бумагу или не принёс вовсе: кадровик печатает сам и не заставляет
    его искать телефон.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="absence_request_application",
        summary="Заявление по заявке для печати",
        parameters=[
            OpenApiParameter("request_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        ],
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    def get(self, request, request_id):
        actor = Actor.from_user(request.user)
        pdf = AbsenceService().hr_application(actor, request_id)
        answer = HttpResponse(pdf, content_type="application/pdf")
        answer["Content-Disposition"] = (
            f'inline; filename="application-{request_id}.pdf"'
        )
        return answer


__all__ = [
    "AbsenceApplicationView",
    "AbsenceApplicationReceivedView",
    "AbsenceDocumentDecisionView",
    "AbsenceDecisionView",
    "AbsencePeriodView",
    "AbsenceDocumentDownloadView",
    "PendingAbsenceRequestsView",
]
