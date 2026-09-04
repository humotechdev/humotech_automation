"""HTTP-слой выгрузок, отдающих файл сразу.

Каждая выгрузка требует `reports.export` ПЛЮС право на сами данные:
право «выгружать» без права «видеть» не открывает ничего. Иначе выгрузка
стала бы обходным путём к данным, закрытым на экране.

Сами строки собирает `reports/sheets.py` — тот же модуль, которым
пользуется фоновая очередь. Отдельного, «быстрого» запроса в обход
области видимости здесь нет и быть не должно.

Этот путь остаётся рядом с очередью, а не заменяется ею. Выгрузка на
сотню строк, отданная сразу, — это один запрос; та же выгрузка через
очередь — заказ, ожидание и скачивание, три действия вместо одного.
Очередь нужна там, где ждать всё равно придётся.
"""

from __future__ import annotations

from datetime import date

from django.http import HttpResponse, StreamingHttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from humotech.analytics.metrics import AnalyticsService
from humotech.attendance.views import _date_param, _uuid_param
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor
from humotech.reports.export import Sheet, to_csv, to_xlsx
from humotech.reports.sheets import EXPORT_KINDS, build_sheet

FORMATS = ("csv", "xlsx")


def parse_filters(request) -> dict:
    """Параметры отчёта из строки запроса, разобранные и проверенные.

    Один разбор на оба пути: сразу отдаваемая выгрузка и заказ в очередь
    понимают параметры одинаково, иначе один и тот же адрес давал бы
    разные файлы в зависимости от способа получения.
    """
    return {
        "date": _date_param(request, "date"),
        "date_from": _date_param(request, "date_from"),
        "date_to": _date_param(request, "date_to"),
        "office_id": _uuid_param(request, "office_id"),
        "region_id": _uuid_param(request, "region_id"),
    }


def author_of(request) -> str:
    """Кто выгрузил — по учётной записи, а не по тому, что прислал клиент."""
    return getattr(request.user, "email", None) or str(request.user.id)


class ExportView(APIView):
    """Выгрузка одного из отчётов в CSV или XLSX.

    Вид отчёта — в пути, а не в теле: в журнале доступа сразу видно,
    что именно выгружали, без разбора параметров запроса.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Выгрузка отчёта файлом",
        description=(
            "kind: employees, attendance, sessions, summary. "
            "Формат задаётся параметром fmt (csv или xlsx) — имя format "
            "занято самим DRF под выбор рендерера. CSV отдаётся потоком "
            "без ограничения размера; у XLSX предел 50 000 строк, при "
            "превышении приходит понятный отказ, а не обрезанный файл.\n\n"
            "Файл собирается прямо в запросе. Для больших выгрузок есть "
            "очередь: POST /api/v1/export-jobs."
        ),
        parameters=[
            OpenApiParameter(
                "kind", str, location=OpenApiParameter.PATH,
                enum=list(EXPORT_KINDS),
            ),
            OpenApiParameter("fmt", str, enum=list(FORMATS)),
            OpenApiParameter("date", str, description="Для отчёта attendance"),
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
            OpenApiParameter("office_id", str),
            OpenApiParameter("region_id", str),
        ],
        responses={
            200: OpenApiResponse(
                description="Файл во вложении (Content-Disposition: attachment)",
            ),
        },
        tags=["Отчёты"],
    )
    def get(self, request, kind: str):
        actor = Actor.from_user(request.user)
        # Право на выгрузку проверяется первым и отдельно от прав на
        # данные: выгрузка — самостоятельное действие, файл уходит из
        # системы и живёт дальше своей жизнью.
        AnalyticsService().access.require(actor, "reports.export")

        # Параметр называется `fmt`, а не `format`, и это не прихоть:
        # `format` DRF разбирает сам как выбор рендерера и отвечает 404
        # раньше, чем управление доходит сюда. Спорить с фреймворком тут
        # дороже, чем взять другое имя.
        fmt = (request.query_params.get("fmt") or "csv").lower()
        if fmt not in FORMATS:
            raise ValidationFailed(
                "Поддерживаются форматы csv и xlsx",
                details={"fmt": fmt, "allowed": list(FORMATS)},
            )

        sheet = build_sheet(
            kind, actor, filters=parse_filters(request),
            author=author_of(request),
        )
        return _respond(sheet, fmt=fmt, kind=kind)


def _respond(sheet: Sheet, *, fmt: str, kind: str):
    stamp = date.today().isoformat()
    name = f"humotech-{kind}-{stamp}.{fmt}"

    if fmt == "csv":
        # Потоком: полумиллионная выгрузка занимает столько же памяти,
        # сколько десять строк.
        response = StreamingHttpResponse(
            (chunk.encode("utf-8") for chunk in to_csv(sheet)),
            content_type="text/csv; charset=utf-8",
        )
    else:
        response = HttpResponse(
            to_xlsx(sheet),
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


__all__ = ["FORMATS", "ExportView", "author_of", "parse_filters"]
