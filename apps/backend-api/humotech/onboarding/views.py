"""REST-интерфейс ознакомления для CRM.

Слой тонкий: разобрать запрос, вызвать сервис, отдать результат. Права
и область видимости проверяют сервисы — если бы это делал view, любой
другой вызывающий обошёл бы проверку молча.

Три группы адресов, потому что это три разных предмета:

  * `onboarding/progress`, `onboarding/counts`, `onboarding/export` —
    кто где остановился;
  * `onboarding/sections` — тексты десяти карточек;
  * `onboarding/documents` — обязательные документы и их редакции.

Плюс вложенный ресурс `employees/<id>/onboarding`: состояние одного
человека кадровик смотрит там же, где всё остальное про него.
"""

from __future__ import annotations

from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.core.api import ServiceViewSet, validated
from humotech.core.errors import NotFound, ValidationFailed
from humotech.core.rbac import Actor
from humotech.files.storage import is_viewable, open_stored, store
from humotech.onboarding.models import PolicyDocumentVersion
from humotech.onboarding.presentation import row_json
from humotech.onboarding.serializers import (
    CrmSectionSerializer,
    DocumentPatchSerializer,
    DocumentSerializer,
    DocumentWriteSerializer,
    InvitationResultSerializer,
    PendingEmployeeSerializer,
    ProgressRowSerializer,
    SectionPatchSerializer,
    SectionWriteSerializer,
    TimelineEventSerializer,
    VersionPatchSerializer,
    VersionSerializer,
    VersionWriteSerializer,
)
from humotech.onboarding.services import (
    OnboardingContentService,
    OnboardingService,
    PolicyService,
)

#: Что можно приложить к редакции документа. Только PDF: утверждённый
#: текст — это бумага с подписью, а не снимок экрана.
POLICY_FILE_TYPES = ("application/pdf",)
#: Столько же, сколько у кадровых документов. Правовой текст на двадцать
#: страниц в десять мегабайт укладывается с запасом.
POLICY_FILE_MAX_BYTES = 10 * 1024 * 1024


def _section_json(row) -> dict:
    return {
        "id": str(row.id),
        "position": row.position,
        "title": row.title,
        "body": row.body,
        "button_label": row.button_label,
        "version": row.version,
        "updated_at": row.updated_at,
    }


def _version_json(row) -> dict:
    return {
        "id": str(row.id),
        "version": row.version,
        "status": row.status,
        "summary": row.summary,
        "body": row.body,
        "agree_label": row.agree_label,
        "has_file": bool(row.file_id),
        "file_name": row.file.original_filename if row.file_id else None,
        "published_at": row.published_at,
        "created_at": row.created_at,
    }


def _document_json(row) -> dict:
    versions = sorted(
        row.versions.all(), key=lambda one: one.created_at, reverse=True
    )
    live = next((one for one in versions if one.status == "PUBLISHED"), None)
    return {
        "id": str(row.id),
        "code": row.code,
        "title": row.title,
        "description": row.description,
        "is_mandatory": row.is_mandatory,
        "position": row.position,
        "archived_at": row.archived_at,
        "current_version": _version_json(live) if live else None,
        "versions": [_version_json(one) for one in versions],
    }


# ----------------------------------------------------------------- прогресс


@extend_schema(tags=["Ознакомление"])
class OnboardingProgressView(APIView):
    """Список сотрудников программы с их прогрессом."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="onboarding_progress",
        summary="Прогресс ознакомления",
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR),
            OpenApiParameter("search", OpenApiTypes.STR),
            OpenApiParameter("office_id", OpenApiTypes.UUID),
            OpenApiParameter("limit", OpenApiTypes.INT),
            OpenApiParameter("cursor", OpenApiTypes.STR),
        ],
        responses={200: ProgressRowSerializer(many=True)},
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        query = request.query_params
        page = OnboardingService().list_progress(
            actor,
            status=query.get("status") or None,
            search=query.get("search") or None,
            office_id=query.get("office_id") or None,
            limit=int(query["limit"]) if query.get("limit") else None,
            cursor=query.get("cursor") or None,
        )
        return Response({
            "items": [row_json(one) for one in page.items],
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        })


@extend_schema(tags=["Ознакомление"])
class OnboardingCountsView(APIView):
    """Счётчики вкладок фильтра."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="onboarding_counts",
        summary="Сколько человек в каком состоянии",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        return Response(OnboardingService().counts(Actor.from_user(request.user)))


@extend_schema(tags=["Ознакомление"])
class OnboardingExportView(APIView):
    """Плоская таблица состояния — для выгрузки.

    Постранично не режется: выгрузка отвечает на вопрос «покажи всех»,
    и файл из первых пятидесяти строк ответом не был бы.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="onboarding_export",
        summary="Выгрузка состояния ознакомления",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        rows = OnboardingService().export_rows(Actor.from_user(request.user))
        return Response({"items": rows, "total": len(rows)})


@extend_schema(tags=["Ознакомление"])
class EmployeeOnboardingView(APIView):
    """Состояние и временная линия одного сотрудника."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="employee_onboarding",
        summary="Ознакомление сотрудника",
        parameters=[
            OpenApiParameter("employee_pk", OpenApiTypes.UUID, OpenApiParameter.PATH),
        ],
        responses={200: ProgressRowSerializer},
    )
    def get(self, request, employee_pk):
        actor = Actor.from_user(request.user)
        service = OnboardingService()
        row = service.employee_state(actor, employee_pk)
        return Response({
            **row_json(row),
            "timeline": TimelineEventSerializer(
                service.timeline(actor, employee_pk), many=True
            ).data,
        })


@extend_schema(tags=["Ознакомление"])
class EmployeeOnboardingActionView(APIView):
    """Действия кадровика по одному сотруднику.

    Действие в пути, а не в теле: каждое из них — отдельное решение с
    отдельной записью в журнале, и различать их по полю в JSON значило бы
    прятать разницу там, где её видно хуже всего.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="employee_onboarding_action",
        summary="Пригласить, отозвать, напомнить, включить в программу",
        parameters=[
            OpenApiParameter("employee_pk", OpenApiTypes.UUID, OpenApiParameter.PATH),
            OpenApiParameter(
                "action", OpenApiTypes.STR, OpenApiParameter.PATH,
                enum=["invite", "reinvite", "revoke", "remind", "enrol"],
            ),
        ],
        request=None,
        responses={200: InvitationResultSerializer},
    )
    def post(self, request, employee_pk, action: str):
        actor = Actor.from_user(request.user)
        service = OnboardingService()

        if action in ("invite", "reinvite"):
            made = service.invite(
                actor, employee_pk, replace=(action == "reinvite")
            )
            invitation = made["invitation"]
            return Response({
                "link": made["link"],
                "expires_at": made["expires_at"],
                "invitation_id": str(invitation.id) if invitation else None,
                "status": invitation.status if invitation else None,
                # Ссылки нет и не будет: человек уже привязан. Это не
                # отказ — в программу он включён.
                "linked": made["linked"],
            }, status=status.HTTP_201_CREATED)

        if action == "revoke":
            invitation = service.revoke(actor, employee_pk)
            return Response({"status": invitation.status})

        if action == "remind":
            return Response(service.remind(actor, employee_pk))

        if action == "enrol":
            row = service.enrol(actor, employee_pk)
            return Response({"status": row.status}, status=status.HTTP_201_CREATED)

        raise NotFound("Неизвестное действие")


# ------------------------------------------------------------------ разделы


class OnboardingSectionViewSet(ServiceViewSet):
    """Тексты десяти карточек. Правятся кадровиком, а не выкатом."""

    service_class = OnboardingContentService
    read_serializer_class = CrmSectionSerializer

    @extend_schema(
        operation_id="onboarding_sections",
        summary="Разделы ознакомления",
        responses={200: CrmSectionSerializer(many=True)},
    )
    def list(self, request):
        rows = self.service.sections(self.actor)
        return Response({"items": [_section_json(one) for one in rows]})

    @extend_schema(
        operation_id="onboarding_section_create",
        request=SectionWriteSerializer,
        responses={201: CrmSectionSerializer},
    )
    def create(self, request):
        data = validated(SectionWriteSerializer, request.data)
        row = self.service.create_section(self.actor, data)
        return Response(_section_json(row), status=status.HTTP_201_CREATED)

    @extend_schema(
        operation_id="onboarding_section_update",
        request=SectionPatchSerializer,
        responses={200: CrmSectionSerializer},
    )
    def partial_update(self, request, pk=None):
        data = validated(SectionPatchSerializer, request.data)
        row = self.service.update_section(self.actor, pk, data)
        return Response(_section_json(row))

    @extend_schema(
        operation_id="onboarding_section_archive",
        request=None,
        responses={200: CrmSectionSerializer},
    )
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return Response(_section_json(self.service.archive_section(self.actor, pk)))


# --------------------------------------------------------------- документы


class PolicyDocumentViewSet(ServiceViewSet):
    """Обязательные документы и их редакции."""

    service_class = PolicyService
    read_serializer_class = DocumentSerializer

    @extend_schema(
        operation_id="policy_documents",
        summary="Обязательные документы",
        responses={200: DocumentSerializer(many=True)},
    )
    def list(self, request):
        rows = self.service.documents(self.actor)
        return Response({"items": [_document_json(one) for one in rows]})

    @extend_schema(
        operation_id="policy_document_create",
        request=DocumentWriteSerializer,
        responses={201: DocumentSerializer},
    )
    def create(self, request):
        data = validated(DocumentWriteSerializer, request.data)
        row = self.service.create_document(self.actor, data)
        return Response(_document_json(row), status=status.HTTP_201_CREATED)

    @extend_schema(
        operation_id="policy_document_update",
        request=DocumentPatchSerializer,
        responses={200: DocumentSerializer},
    )
    def partial_update(self, request, pk=None):
        data = validated(DocumentPatchSerializer, request.data)
        row = self.service.update_document(self.actor, pk, data)
        return Response(_document_json(row))

    @extend_schema(
        operation_id="policy_document_archive", request=None,
        responses={200: DocumentSerializer},
    )
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return Response(_document_json(self.service.archive_document(self.actor, pk)))

    @extend_schema(
        operation_id="policy_document_version_create",
        summary="Новая редакция (черновик)",
        request=VersionWriteSerializer,
        responses={201: VersionSerializer},
    )
    @action(detail=True, methods=["post"], url_path="versions")
    def versions(self, request, pk=None):
        data = validated(VersionWriteSerializer, request.data)
        row = self.service.create_version(self.actor, pk, data)
        return Response(_version_json(row), status=status.HTTP_201_CREATED)

    @extend_schema(
        operation_id="policy_document_pending",
        summary="Кто ещё не подтвердил действующую редакцию",
        responses={200: PendingEmployeeSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def pending(self, request, pk=None):
        rows = self.service.pending_employees(self.actor, pk)
        return Response({"items": rows, "total": len(rows)})


@extend_schema(tags=["Ознакомление"])
class PolicyVersionView(APIView):
    """Правка черновика и публикация редакции."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="policy_version_update",
        summary="Правка черновика редакции",
        request=VersionPatchSerializer,
        responses={200: VersionSerializer},
    )
    def patch(self, request, version_id):
        data = validated(VersionPatchSerializer, request.data)
        row = PolicyService().update_version(
            Actor.from_user(request.user), version_id, data
        )
        return Response(_version_json(row))


@extend_schema(tags=["Ознакомление"])
class PolicyVersionPublishView(APIView):
    """Выпустить редакцию.

    Самое тяжёлое действие раздела: с этой секунды все, кто не подтвердил
    новый текст, теряют рабочие функции бота, пока не подтвердят.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="policy_version_publish",
        summary="Опубликовать редакцию",
        request=None,
        responses={200: VersionSerializer},
    )
    def post(self, request, version_id):
        row = PolicyService().publish_version(
            Actor.from_user(request.user), version_id
        )
        return Response(_version_json(row))


@extend_schema(tags=["Ознакомление"])
class PolicyVersionFileView(APIView):
    """Утверждённый PDF редакции: загрузка и выдача.

    Загрузка — только в черновик. Подменить файл под опубликованным
    текстом значило бы подменить то, с чем согласились люди.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="policy_version_file_upload",
        summary="Приложить PDF к редакции",
        request={"multipart/form-data": {"type": "object", "properties": {
            "document": {"type": "string", "format": "binary"}}}},
        responses={200: VersionSerializer},
    )
    def post(self, request, version_id):
        actor = Actor.from_user(request.user)
        service = PolicyService()
        upload = request.FILES.get("document")
        if upload is None:
            raise ValidationFailed("Файл не приложен", details={"field": "document"})

        version = service._require_version(actor, version_id)
        if version.status != "DRAFT":
            from humotech.core.errors import Conflict

            raise Conflict(
                "Файл прикладывается к черновику: опубликованную редакцию "
                "менять нельзя",
                details={"status": version.status},
            )
        stored = store(
            upload,
            organization_id=actor.organization_id,
            employee=None,
            allowed_types=POLICY_FILE_TYPES,
            max_bytes=POLICY_FILE_MAX_BYTES,
            prefix="policies",
            user_id=actor.user_id,
        )
        row = service.update_version(
            actor, version_id, {"file_id": stored.file.id}
        )
        return Response(_version_json(row))

    @extend_schema(
        operation_id="policy_version_file_download",
        summary="Скачать PDF редакции",
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    def get(self, request, version_id):
        actor = Actor.from_user(request.user)
        service = PolicyService()
        service.access.require(actor, "onboarding.read")
        version = PolicyDocumentVersion.objects.select_related("file").filter(
            id=version_id, organization_id=actor.organization_id
        ).first()
        if version is None or version.file_id is None:
            raise NotFound("Файл не найден")
        if not is_viewable(version.file):
            raise NotFound("Файл документа недоступен")
        return FileResponse(
            open_stored(version.file),
            as_attachment=False,
            filename=version.file.original_filename,
            content_type=version.file.mime_type,
        )


__all__ = [
    "EmployeeOnboardingActionView",
    "EmployeeOnboardingView",
    "OnboardingCountsView",
    "OnboardingExportView",
    "OnboardingProgressView",
    "OnboardingSectionViewSet",
    "PolicyDocumentViewSet",
    "PolicyVersionFileView",
    "PolicyVersionPublishView",
    "PolicyVersionView",
]
