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

from django.http import FileResponse, HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.absences.services import AbsenceService
from humotech.core.api import validated
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor


class DecisionSerializer(serializers.Serializer):
    comment = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
        help_text="Основание решения. Уходит сотруднику в чат",
    )


class AbsenceEmployeeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    employee_number = serializers.CharField(allow_null=True)


class AbsenceTypeBriefSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()


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
    status = serializers.CharField()
    first_day = serializers.DateField(allow_null=True)
    last_day = serializers.DateField(allow_null=True)
    comment = serializers.CharField(allow_null=True)
    review_comment = serializers.CharField(allow_null=True)
    submitted_at = serializers.DateTimeField(allow_null=True)
    history = serializers.ListField(
        child=serializers.DictField(),
        help_text=(
            "Шаги заявки по порядку: at, action, comment. Комментарий есть "
            "только у решений кадровика"
        ),
    )


class PendingAbsenceRequestsSerializer(serializers.Serializer):
    requests = HrAbsenceRequestSerializer(many=True)


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
                "comment": (
                    action.comment
                    if action.action in HR_DECISION_ACTIONS
                    else None
                ),
            }
            for action in sorted(request.actions.all(), key=lambda one: one.created_at)
        ],
    }


# Шаги, комментарий к которым пишет кадровик, а не сотрудник.
HR_DECISION_ACTIONS = ("APPROVED", "REJECTED", "CANCELLED")


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
        return Response({"requests": [hr_request_json(row) for row in rows]})


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
        return FileResponse(
            stream,
            as_attachment=False,
            filename=record.original_filename,
            content_type=record.mime_type,
        )


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
    "AbsenceDocumentDecisionView",
    "AbsenceDecisionView",
    "AbsenceDocumentDownloadView",
    "PendingAbsenceRequestsView",
]
