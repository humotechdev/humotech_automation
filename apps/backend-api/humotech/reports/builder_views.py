"""REST конструктора отчётов: каталог полей, предпросмотр и шаблоны.

Заказ файла идёт через `/export-jobs/` — туда же, куда и раньше: очередь
одна, история одна. Здесь только то, что нужно странице до заказа.
"""

from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.core.api import ServiceViewSet, validated
from humotech.core.rbac import Actor
from humotech.reports.builder import (
    MAX_CHOICES, PERIOD_MODES, PREVIEW_MAX_ROWS, PREVIEW_MIN_ROWS,
    ReportBuilderService, ReportSpec,
)
from humotech.reports.catalog import (
    COLUMN_TYPES, FIELDS, KIND_PERMISSIONS, KIND_TITLES, REPORT_KINDS,
)
from humotech.reports.export import XLSX_MAX_ROWS
from humotech.reports.sheets import MAX_PERIOD_DAYS
from humotech.reports.templates import TEMPLATE_NAME_MAX, ReportTemplateService
from humotech.reports.views import FORMATS


class ReportSpecSerializer(serializers.Serializer):
    """Параметры конструктора. Один набор на предпросмотр, заказ и шаблон."""

    date_from = serializers.DateField()
    date_to = serializers.DateField(
        help_text=f"Не раньше date_from, период не длиннее {MAX_PERIOD_DAYS} дней",
    )
    period = serializers.ChoiceField(
        choices=PERIOD_MODES, required=False, default="custom",
        help_text="Правило периода для шаблона: этот месяц, прошлый или свои даты",
    )
    region_id = serializers.UUIDField(required=False, allow_null=True)
    office_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list,
        max_length=MAX_CHOICES, help_text="Пусто — все доступные офисы",
    )
    department_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list,
        max_length=MAX_CHOICES, help_text="Пусто — все отделы",
    )
    employee_id = serializers.UUIDField(
        required=False, allow_null=True, help_text="Пусто — все сотрудники",
    )
    include_inactive = serializers.BooleanField(required=False, default=False)
    fields = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False,
        help_text="Ключи полей из каталога. Не передано — поля по умолчанию",
    )
    name = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=200,
        help_text="Имя файла без расширения. Пусто — вид и период",
    )


def spec_from(kind: str, payload: dict) -> ReportSpec:
    return ReportSpec.build(
        kind,
        date_from=payload["date_from"],
        date_to=payload["date_to"],
        region_id=payload.get("region_id"),
        office_ids=payload.get("office_ids") or (),
        department_ids=payload.get("department_ids") or (),
        employee_id=payload.get("employee_id"),
        include_inactive=payload.get("include_inactive", False),
        fields=payload.get("fields"),
        name=payload.get("name"),
        period=payload.get("period"),
    )


# --- каталог ------------------------------------------------------------------


class CatalogFieldSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    default = serializers.BooleanField()
    columns = serializers.ListField(child=serializers.CharField())


class CatalogKindSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=REPORT_KINDS)
    title = serializers.CharField()
    permission = serializers.CharField()
    fields = CatalogFieldSerializer(many=True)


class ReportCatalogSerializer(serializers.Serializer):
    kinds = CatalogKindSerializer(many=True)
    max_period_days = serializers.IntegerField()
    xlsx_max_rows = serializers.IntegerField()
    retention_hours = serializers.IntegerField()
    preview_min_rows = serializers.IntegerField()
    preview_max_rows = serializers.IntegerField()


@extend_schema(tags=["Отчёты"])
class ReportCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Виды отчётов и их поля",
                   responses={200: ReportCatalogSerializer})
    def get(self, request):
        ReportBuilderService().access.require(
            Actor.from_user(request.user), "reports.export")
        return Response({
            "kinds": [
                {
                    "key": kind,
                    "title": KIND_TITLES[kind],
                    "permission": KIND_PERMISSIONS[kind],
                    "fields": [
                        {"key": item.key, "title": item.title, "default": item.default,
                         "columns": [column.title for column in item.columns]}
                        for item in FIELDS[kind]
                    ],
                }
                for kind in REPORT_KINDS
            ],
            "max_period_days": MAX_PERIOD_DAYS,
            "xlsx_max_rows": XLSX_MAX_ROWS,
            "retention_hours": settings.EXPORTS["RETENTION_HOURS"],
            "preview_min_rows": PREVIEW_MIN_ROWS,
            "preview_max_rows": PREVIEW_MAX_ROWS,
        })


# --- предпросмотр -------------------------------------------------------------


class ReportPreviewRequestSerializer(ReportSpecSerializer):
    kind = serializers.ChoiceField(choices=REPORT_KINDS)
    fmt = serializers.ChoiceField(choices=FORMATS, default="xlsx")
    limit = serializers.IntegerField(
        min_value=PREVIEW_MIN_ROWS, max_value=PREVIEW_MAX_ROWS,
        default=PREVIEW_MAX_ROWS,
    )


class PreviewColumnSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    type = serializers.ChoiceField(choices=COLUMN_TYPES)


class PreviewCellSerializer(serializers.Serializer):
    text = serializers.CharField(allow_null=True)
    tone = serializers.CharField(allow_null=True)


class ReportPreviewSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=REPORT_KINDS)
    title = serializers.CharField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    fmt = serializers.ChoiceField(choices=FORMATS)
    file_name = serializers.CharField()
    offices = serializers.IntegerField()
    employees = serializers.IntegerField()
    employee_name = serializers.CharField(
        allow_null=True, help_text="ФИО выбранного сотрудника, если он выбран",
    )
    days = serializers.IntegerField(allow_null=True)
    rows_estimate = serializers.IntegerField()
    estimate_exact = serializers.BooleanField(
        help_text="false — оценка по первым дням периода (опоздания)",
    )
    sampled_days = serializers.IntegerField(allow_null=True)
    columns = PreviewColumnSerializer(many=True)
    rows = serializers.ListField(child=PreviewCellSerializer(many=True))
    sheets = serializers.ListField(child=serializers.CharField())
    timezones = serializers.ListField(child=serializers.CharField())
    warnings = serializers.ListField(child=serializers.CharField())


@extend_schema(tags=["Отчёты"])
class ReportPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Предпросмотр отчёта",
        description=(
            f"Первые {PREVIEW_MIN_ROWS}–{PREVIEW_MAX_ROWS} строк настоящих данных "
            "и оценка объёма. Для отчётов по дням просматриваются первые дни "
            "периода, а не весь период: большая выборка не превращает "
            "предпросмотр в сборку файла."
        ),
        request=ReportPreviewRequestSerializer,
        responses={200: ReportPreviewSerializer},
    )
    def post(self, request):
        payload = validated(ReportPreviewRequestSerializer, request.data)
        spec = spec_from(payload["kind"], payload)
        result = ReportBuilderService().preview(
            Actor.from_user(request.user), spec,
            fmt=payload["fmt"], limit=payload["limit"],
        )
        return Response(ReportPreviewSerializer(result).data)


# --- шаблоны ------------------------------------------------------------------


class ReportTemplateSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=REPORT_KINDS)
    fmt = serializers.ChoiceField(choices=FORMATS)
    filters = serializers.JSONField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ReportTemplateListSerializer(serializers.Serializer):
    items = ReportTemplateSerializer(many=True)


class ReportTemplateWriteSerializer(ReportSpecSerializer):
    template_name = serializers.CharField(max_length=TEMPLATE_NAME_MAX)
    kind = serializers.ChoiceField(choices=REPORT_KINDS)
    fmt = serializers.ChoiceField(choices=FORMATS, default="xlsx")


@extend_schema(tags=["Отчёты"])
class ReportTemplateViewSet(ServiceViewSet):
    """Личные шаблоны конструктора. Требует `reports.export`."""

    service_class = ReportTemplateService
    read_serializer_class = ReportTemplateSerializer

    @extend_schema(summary="Мои шаблоны отчётов",
                   responses={200: ReportTemplateListSerializer})
    def list(self, request):
        items = self.service.list(self.actor)
        return Response({"items": ReportTemplateSerializer(items, many=True).data})

    @extend_schema(
        summary="Сохранить шаблон",
        description="Шаблон с тем же названием перезаписывается.",
        request=ReportTemplateWriteSerializer,
        responses={200: ReportTemplateSerializer, 201: ReportTemplateSerializer},
    )
    def create(self, request):
        payload = validated(ReportTemplateWriteSerializer, request.data)
        template, created = self.service.save(
            self.actor,
            name=payload["template_name"],
            fmt=payload["fmt"],
            spec=spec_from(payload["kind"], payload),
        )
        return self.item_response(template, created=created)

    @extend_schema(summary="Удалить шаблон", request=None, responses={204: None})
    def destroy(self, request, pk=None):
        self.service.delete(self.actor, pk)
        return Response(status=204)


__all__ = [
    "ReportCatalogView", "ReportPreviewView", "ReportSpecSerializer",
    "ReportTemplateViewSet", "spec_from",
]
