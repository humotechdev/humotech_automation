"""REST-интерфейс фоновых выгрузок.

Отдельно от `reports/views.py`: там выгрузка, отдающая файл сразу, здесь
заказ и его состояние. Оба пути существуют одновременно и не подменяют
друг друга — короткий отчёт незачем прогонять через очередь.
"""

from __future__ import annotations

from django.http import FileResponse
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import action

from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import EXPORT_JOB_STATUSES
from humotech.reports.service import ExportJobService
from humotech.reports.sheets import EXPORT_KINDS
from humotech.reports.views import FORMATS


class ExportJobSerializer(serializers.Serializer):
    """Состояние заказа.

    `storage_key` здесь нет намеренно: это путь на диске сервера, клиенту
    он не нужен, а показанный путь однажды окажется в чьём-нибудь запросе.
    """

    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=EXPORT_KINDS)
    fmt = serializers.ChoiceField(choices=FORMATS)
    status = serializers.ChoiceField(choices=EXPORT_JOB_STATUSES)
    filters = serializers.JSONField(allow_null=True)
    requested_by_user_id = serializers.UUIDField()
    attempts = serializers.IntegerField()
    progress_rows = serializers.IntegerField(
        help_text="Сколько строк уже записано. «Идёт» без числа "
                  "неотличимо от «умерла»",
    )
    total_rows = serializers.IntegerField(
        allow_null=True,
        help_text="Известно не всегда: CSV собирается потоком, и общее "
                  "число строк заранее не считается",
    )
    file_name = serializers.CharField(allow_null=True)
    size_bytes = serializers.IntegerField(allow_null=True)
    expires_at = serializers.DateTimeField(
        allow_null=True, help_text="После этого момента файл удаляется",
    )
    error_message = serializers.CharField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ExportJobPageSerializer(serializers.Serializer):
    items = ExportJobSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class ExportJobCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=EXPORT_KINDS)
    fmt = serializers.ChoiceField(choices=FORMATS, default="csv")
    date = serializers.DateField(
        required=False, allow_null=True, help_text="Для отчёта attendance",
    )
    date_from = serializers.DateField(required=False, allow_null=True)
    date_to = serializers.DateField(required=False, allow_null=True)
    office_id = serializers.UUIDField(required=False, allow_null=True)
    region_id = serializers.UUIDField(required=False, allow_null=True)


@extend_schema(tags=["Отчёты"])
class ExportJobViewSet(ServiceViewSet):
    """Заказы на выгрузку. Требует `reports.export`.

    Скачать готовый файл может только тот, кто его заказал: файл собран
    по ЕГО области видимости, и коллеге с другой областью он показал бы
    данные, закрытые для того на экране.
    """

    service_class = ExportJobService
    read_serializer_class = ExportJobSerializer

    @extend_schema(
        summary="Мои выгрузки",
        parameters=[
            OpenApiParameter("status", str, enum=list(EXPORT_JOB_STATUSES)),
            OpenApiParameter("kind", str, enum=list(EXPORT_KINDS)),
            OpenApiParameter(
                "mine_only", bool,
                description="false — все выгрузки организации; требует "
                            "права audit.read, иначе список остаётся своим",
            ),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
        responses={200: ExportJobPageSerializer},
    )
    def list(self, request):
        params = self.list_params()
        params.pop("search", None)  # у задания нет текста для поиска
        return self.page_response(
            self.service.list(
                self.actor,
                **params,
                kind=request.query_params.get("kind") or None,
                mine_only=request.query_params.get("mine_only") != "false",
            )
        )

    @extend_schema(summary="Состояние выгрузки")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    @extend_schema(
        summary="Заказать выгрузку",
        description=(
            "Параметры проверяются сразу: ошибка в них не должна "
            "превращаться в задание, которое выглядит принятым и "
            "умирает через минуту.\n\n"
            "Права на данные проверяются В МОМЕНТ сборки, а не заказа: "
            "выгрузка, заказанная кадровиком всей компании и собранная "
            "после перевода его в один офис, соберётся по новым правам."
        ),
        request=ExportJobCreateSerializer,
        responses={201: ExportJobSerializer},
        examples=[
            OpenApiExample(
                "Сессии за квартал в CSV",
                value={"kind": "sessions", "fmt": "csv",
                       "date_from": "2026-01-01", "date_to": "2026-03-31"},
                request_only=True,
            ),
        ],
    )
    def create(self, request):
        payload = validated(ExportJobCreateSerializer, request.data)
        kind = payload.pop("kind")
        fmt = payload.pop("fmt")
        return self.item_response(
            self.service.create(self.actor, kind=kind, fmt=fmt, filters=payload),
            created=True,
        )

    @extend_schema(
        summary="Отменить заказ",
        description=(
            "Только пока выгрузка не начата. Строку, которую прямо сейчас "
            "собирает исполнитель, отменить нельзя: «отменено» рядом "
            "с собираемым файлом было бы неправдой."
        ),
        request=None,
        responses={200: ExportJobSerializer},
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self.item_response(self.service.cancel(self.actor, pk))

    @extend_schema(
        summary="Собрать заново",
        description="Счётчик попыток обнуляется, старый файл удаляется.",
        request=None,
        responses={200: ExportJobSerializer},
    )
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        return self.item_response(self.service.retry(self.actor, pk))

    @extend_schema(
        summary="Скачать готовый файл",
        description=(
            "Доступно только заказчику и только пока не истёк срок "
            "хранения. Просроченный файл удаляется: выгрузка кадровых "
            "данных, лежащая вечно, — это утечка, отложенная во времени."
        ),
        responses={
            200: OpenApiResponse(
                description="Файл во вложении (Content-Disposition: attachment)",
            ),
        },
    )
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        stream, job = self.service.open_file(self.actor, pk)
        response = FileResponse(
            stream,
            as_attachment=True,
            filename=job.file_name or f"humotech-{job.kind}.{job.fmt}",
            content_type=(
                "text/csv; charset=utf-8"
                if job.fmt == "csv"
                else "application/vnd.openxmlformats-officedocument."
                     "spreadsheetml.sheet"
            ),
        )
        return response


__all__ = ["ExportJobViewSet"]
