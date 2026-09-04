"""Построители отчётов: из данных — в готовую к выгрузке таблицу.

Отдельно от HTTP намеренно. Один и тот же отчёт собирают двое: view,
отдающий файл сразу, и фоновый исполнитель очереди. У второго нет и не
может быть `request`, поэтому параметры приходят разобранным словарём,
а не из строки запроса.

Разбирает и проверяет их та сторона, которая принимает заказ. Проверка
только в исполнителе превратила бы ошибку в параметрах в задание, которое
выглядит принятым и умирает через минуту, — а человек к тому моменту уже
ушёл с экрана.

Область видимости здесь не обходится: строки собирают те же сервисы, что
и экраны, с теми же проверками. Причём права проверяются В МОМЕНТ сборки,
а не заказа: выгрузка, заказанная кадровиком всей компании и выполненная
после перевода его в один офис, обязана собраться по НОВЫМ правам.
"""

from __future__ import annotations

from datetime import date

from humotech.analytics.metrics import AnalyticsService
from humotech.attendance.hr import AttendanceHrService
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor
from humotech.employees.services import EmployeeService
from humotech.reports.export import Sheet, base_meta, formula_meta

#: Виды отчётов. Список один на весь проект: и для проверки заказа,
#: и для схемы OpenAPI, и для исполнителя очереди.
EXPORT_KINDS = ("employees", "attendance", "sessions", "summary")


def build_sheet(
    kind: str, actor: Actor, *, filters: dict, author: str
) -> Sheet:
    """Собрать таблицу отчёта. Единственный вход в этот модуль."""
    builder = {
        "employees": _employees,
        "attendance": _attendance,
        "sessions": _sessions,
        "summary": _summary,
    }.get(kind)
    if builder is None:
        raise ValidationFailed(
            "Неизвестный отчёт",
            details={"kind": kind, "allowed": list(EXPORT_KINDS)},
        )
    return builder(actor, filters, author)


def _employees(actor: Actor, filters: dict, author: str) -> Sheet:
    service = EmployeeService()
    office_id = filters.get("office_id")
    region_id = filters.get("region_id")

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
            author=author,
            filters={"office_id": office_id, "region_id": region_id},
        ),
    )

def _attendance(actor: Actor, filters: dict, author: str) -> Sheet:
    service = AttendanceHrService()
    day = filters.get("date") or date.today()
    office_id = filters.get("office_id")
    region_id = filters.get("region_id")

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
            author=author,
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

def _sessions(actor: Actor, filters: dict, author: str) -> Sheet:
    service = AttendanceHrService()
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    office_id = filters.get("office_id")
    region_id = filters.get("region_id")

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
            author=author,
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

def _summary(actor: Actor, filters: dict, author: str) -> Sheet:
    """Сводка по офисам: по строке на офис, с формулами в шапке."""
    service = AnalyticsService()
    first = filters.get("date_from")
    last = filters.get("date_to")
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
        author=author,
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


def _full_name(employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


__all__ = ["EXPORT_KINDS", "build_sheet"]
