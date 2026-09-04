"""HTTP-слой выгрузок.

Каждая выгрузка требует `reports.export` ПЛЮС право на сами данные:
право «выгружать» без права «видеть» не открывает ничего. Иначе выгрузка
стала бы обходным путём к данным, закрытым на экране.

Сами строки собираются теми же сервисами, что и экраны. Отдельного,
«быстрого» запроса в обход области видимости здесь нет и быть не должно.
"""

from __future__ import annotations

from datetime import date

from django.http import HttpResponse, StreamingHttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from humotech.analytics.metrics import AnalyticsService
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.views import _date_param, _uuid_param
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor
from humotech.employees.services import EmployeeService
from humotech.reports.export import (
    Sheet,
    base_meta,
    formula_meta,
    to_csv,
    to_xlsx,
)

FORMATS = ("csv", "xlsx")


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
            "превышении приходит понятный отказ, а не обрезанный файл."
        ),
        parameters=[
            OpenApiParameter(
                "kind", str, location=OpenApiParameter.PATH,
                enum=["employees", "attendance", "sessions", "summary"],
            ),
            OpenApiParameter("fmt", str, enum=["csv", "xlsx"]),
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

        builder = {
            "employees": self._employees,
            "attendance": self._attendance,
            "sessions": self._sessions,
            "summary": self._summary,
        }.get(kind)
        if builder is None:
            raise ValidationFailed(
                "Неизвестный отчёт",
                details={"kind": kind, "allowed": ["employees", "attendance",
                                                   "sessions", "summary"]},
            )

        sheet = builder(actor, request)
        return _respond(sheet, fmt=fmt, kind=kind)

    # ------------------------------------------------------------- отчёты

    def _employees(self, actor: Actor, request) -> Sheet:
        service = EmployeeService()
        office_id = _uuid_param(request, "office_id")
        region_id = _uuid_param(request, "region_id")

        def rows():
            cursor = None
            while True:
                page = service.list(
                    actor, office_id=office_id, region_id=region_id,
                    limit=200, cursor=cursor,
                )
                for employee in page.items:
                    assignment = getattr(employee, "current_assignment", None)
                    yield [
                        employee.employee_number,
                        _full_name(employee),
                        employee.employment_status,
                        assignment.office.name if assignment and assignment.office
                        else None,
                        assignment.department.name
                        if assignment and assignment.department else None,
                        assignment.position.name
                        if assignment and assignment.position else None,
                        employee.hire_date,
                    ]
                if not page.has_more:
                    return
                cursor = page.next_cursor

        return Sheet(
            title="Сотрудники",
            columns=["Табельный номер", "ФИО", "Статус", "Офис", "Отдел",
                     "Должность", "Дата приёма"],
            rows=rows(),
            meta=base_meta(
                title="Сотрудники",
                author=_author(request),
                filters={"office_id": office_id, "region_id": region_id},
            ),
        )

    def _attendance(self, actor: Actor, request) -> Sheet:
        service = AttendanceHrService()
        day = _date_param(request, "date") or date.today()
        office_id = _uuid_param(request, "office_id")
        region_id = _uuid_param(request, "region_id")

        report = service.presence(
            actor, day=day, office_id=office_id, region_id=region_id
        )
        rows = [
            [
                row.employee_number,
                row.full_name,
                row.office_name,
                row.state,
                row.first_entry_at,
                row.last_exit_at,
                round(row.seconds / 3600, 2),
                row.late_minutes,
                row.absence_name,
            ]
            for row in report.rows
        ]
        return Sheet(
            title="Посещаемость за день",
            columns=["Табельный номер", "ФИО", "Офис", "Состояние", "Вход",
                     "Выход", "Часов", "Опоздание, мин", "Отсутствие"],
            rows=rows,
            meta=base_meta(
                title="Посещаемость за день",
                author=_author(request),
                period=(day, day),
                timezone=report.timezone,
                filters={"office_id": office_id, "region_id": region_id},
            )
            + [
                (
                    "Пустое «Опоздание»",
                    "означает «сравнивать не с чем»: у сотрудника нет графика "
                    "или он не приходил. Это не ноль минут.",
                ),
            ],
        )

    def _sessions(self, actor: Actor, request) -> Sheet:
        service = AttendanceHrService()
        date_from = _date_param(request, "date_from")
        date_to = _date_param(request, "date_to")
        office_id = _uuid_param(request, "office_id")
        region_id = _uuid_param(request, "region_id")

        def rows():
            cursor = None
            while True:
                page = service.sessions(
                    actor, date_from=date_from, date_to=date_to,
                    office_id=office_id, region_id=region_id,
                    limit=200, cursor=cursor,
                )
                for session in page.items:
                    yield [
                        session.employee.employee_number,
                        _full_name(session.employee),
                        session.office.name if session.office else None,
                        session.started_at,
                        session.ended_at,
                        round((session.duration_seconds or 0) / 3600, 2),
                        session.status,
                        session.ended_at is None,
                    ]
                if not page.has_more:
                    return
                cursor = page.next_cursor

        return Sheet(
            title="Рабочие сессии",
            columns=["Табельный номер", "ФИО", "Офис", "Начало", "Конец",
                     "Часов", "Статус", "Не закрыта"],
            rows=rows(),
            meta=base_meta(
                title="Рабочие сессии",
                author=_author(request),
                period=(date_from, date_to) if date_from and date_to else None,
                filters={"office_id": office_id, "region_id": region_id},
            )
            + [
                (
                    "Незакрытая сессия",
                    "показана «по состоянию на сейчас»; выдуманного времени "
                    "выхода в отчёте нет.",
                ),
            ],
        )

    def _summary(self, actor: Actor, request) -> Sheet:
        """Сводка по офисам: по строке на офис, с формулами в шапке."""
        service = AnalyticsService()
        first = _date_param(request, "date_from")
        last = _date_param(request, "date_to")
        if not first or not last:
            from humotech.core.timeframes import month_range

            first, last = month_range(date.today())

        offices = service._offices(actor)  # noqa: SLF001 — тот же сервис
        reports = [
            service.office(actor, office.id, first=first, last=last)
            for office in offices
        ]

        rows = []
        for report in reports:
            ratios = {ratio.key: ratio for ratio in report.ratios}
            attendance = ratios["attendance"]
            punctuality = ratios["punctuality"]
            rows.append(
                [
                    report.scope_name,
                    report.headcount,
                    report.totals["expected_working_days"],
                    report.totals["attended_days"],
                    report.totals["missed_days"],
                    attendance.percent,
                    f"{attendance.numerator} / {attendance.denominator}",
                    punctuality.percent,
                    f"{punctuality.numerator} / {punctuality.denominator}",
                    round(report.totals["worked_seconds"] / 3600, 2),
                    report.totals["late_arrivals"],
                    report.coverage.ratio,
                ]
            )

        meta = base_meta(
            title="Сводка по офисам",
            author=_author(request),
            period=(first, last),
            timezone=reports[0].timezone if reports else None,
            filters={},
        )
        if reports:
            meta += formula_meta(reports[0].ratios)
        meta.append(
            (
                "Пустой процент",
                "означает «нет данных»: в периоде не было рабочих дней или "
                "ни у кого нет графика. Это не ноль процентов.",
            )
        )

        return Sheet(
            title="Сводка по офисам",
            columns=["Офис", "Штат", "Рабочих дней", "С отметками", "Пропущено",
                     "Посещаемость, %", "Посещаемость: дробь",
                     "Приход вовремя, %", "Вовремя: дробь", "Часов в офисе",
                     "Опозданий", "Покрытие графиками, %"],
            rows=rows,
            meta=meta,
        )


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


def _author(request) -> str:
    """Кто выгрузил — по учётной записи, а не по тому, что прислал клиент."""
    return getattr(request.user, "email", None) or str(request.user.id)


def _full_name(employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)
