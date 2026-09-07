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

from humotech.absences.services import AbsenceService
from humotech.analytics.metrics import AnalyticsService
from humotech.attendance.hr import AttendanceHrService
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor
from humotech.core.timeframes import days_in, month_range
from humotech.employees.services import EmployeeService
from humotech.reports.export import Sheet, base_meta, formula_meta

#: Виды отчётов. Список один на весь проект: и для проверки заказа,
#: и для схемы OpenAPI, и для исполнителя очереди.
EXPORT_KINDS = (
    "employees", "attendance", "sessions", "summary", "lateness", "absences",
)

#: Сколько дней помещается в один отчёт по дням.
#:
#: Отчёты, идущие по дням, спрашивают присутствие у сервиса на каждую дату
#: отдельно — иначе пришлось бы повторить его правила второй раз и в
#: другом месте. Год с запасом на високосный — предел, за которым запрос
#: перестаёт быть отчётом и становится выгрузкой всей базы.
MAX_PERIOD_DAYS = 366


def build_sheet(
    kind: str, actor: Actor, *, filters: dict, author: str
) -> Sheet:
    """Собрать таблицу отчёта. Единственный вход в этот модуль."""
    builder = {
        "employees": _employees,
        "attendance": _attendance,
        "sessions": _sessions,
        "summary": _summary,
        "lateness": _lateness,
        "absences": _absences,
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
    """Присутствие: один день или период по дням.

    Период собирается тем же `presence()`, что и экран, — по дате за раз.
    Дороже, чем один запрос, зато правила состояния, опоздания и
    отсутствия остаются в одном месте. Второй их реализации, «быстрой,
    зато для отчёта», в проекте нет и не должно быть.
    """
    service = AttendanceHrService()
    office_id = filters.get("office_id")
    region_id = filters.get("region_id")
    first, last = _period(filters, fallback_single_day=True)
    single = first == last

    columns = ["Табельный номер", "ФИО", "Офис", "Состояние", "Вход",
               "Выход", "Часов", "Опоздание, мин", "Отсутствие"]
    if not single:
        columns.insert(0, "Дата")

    # Первый день спрашивается сразу и отдельно: часовой пояс нужен
    # в шапке файла, а шапка пишется раньше строк. Ответ не выбрасывается
    # — он же становится первым куском таблицы, второго запроса за тот
    # же день нет.
    head = service.presence(
        actor, day=first, office_id=office_id, region_id=region_id
    )

    def rows():
        for day, report in _presence_days(
            service, actor, first, last,
            office_id=office_id, region_id=region_id, first_report=head,
        ):
            for row in report.rows:
                line = [
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
                if not single:
                    line.insert(0, day)
                yield line

    title = "Посещаемость за день" if single else "Посещаемость"
    return Sheet(
        title=title,
        columns=columns,
        rows=rows(),
        meta=base_meta(
            title=title,
            author=author,
            period=(first, last),
            timezone=head.timezone,
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
            # Границы периода этот отчёт берёт по поясу ОРГАНИЗАЦИИ, а не
            # офиса: у списка сессий может не быть одного офиса, и сутки
            # надо чем-то ограничить. Пояс называется в файле — иначе
            # «01 августа» в отчёте по нескольким офисам означало бы
            # разное для разных читателей.
            timezone=str(service._organization_zone(actor)),  # noqa: SLF001
            filters={"office_id": office_id, "region_id": region_id},
        )
        + [
            (
                "Незакрытая сессия",
                "показана «по состоянию на сейчас»; выдуманного времени "
                "выхода в отчёте нет.",
            ),
            (
                "Границы периода",
                "отсчитаны по часовому поясу организации, а не офиса: "
                "в выборку может попасть несколько офисов с разными "
                "поясами.",
            ),
        ],
    )

def _summary(actor: Actor, filters: dict, author: str) -> Sheet:
    """Сводка по офисам: по строке на офис, с формулами в шапке."""
    service = AnalyticsService()
    first = filters.get("date_from")
    last = filters.get("date_to")
    if not first or not last:
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


def _lateness(actor: Actor, filters: dict, author: str) -> Sheet:
    """Опоздания за период — по данным графика, и только по ним.

    Строка появляется, лишь когда опоздание ИЗМЕРЕНО. `late_minutes is
    None` — это «сравнивать не с чем»: у человека нет графика на этот
    день или он вовсе не приходил. Превратить такой день в опоздание
    значит обвинить сотрудника в том, чего никто не считал.

    Ноль минут — тоже не опоздание: пришёл ровно вовремя. В отчёт он не
    идёт, но и «опоздавшим» нигде не назван.
    """
    service = AttendanceHrService()
    office_id = filters.get("office_id")
    region_id = filters.get("region_id")
    first, last = _period(filters, fallback_single_day=False)

    head = service.presence(
        actor, day=first, office_id=office_id, region_id=region_id
    )

    def rows():
        for day, report in _presence_days(
            service, actor, first, last,
            office_id=office_id, region_id=region_id, first_report=head,
        ):
            for row in report.rows:
                if row.late_minutes is None or row.late_minutes <= 0:
                    continue
                yield [
                    day,
                    row.employee_number,
                    row.full_name,
                    row.office_name,
                    row.scheduled_start,
                    row.first_entry_at,
                    row.late_minutes,
                ]

    return Sheet(
        title="Опоздания",
        columns=["Дата", "Табельный номер", "ФИО", "Офис", "Начало по графику",
                 "Первый вход", "Опоздание, мин"],
        rows=rows(),
        meta=base_meta(
            title="Опоздания",
            author=author,
            period=(first, last),
            timezone=head.timezone,
            filters={"office_id": office_id, "region_id": region_id},
        )
        + [
            (
                "Что считается опозданием",
                "первый вход позже начала смены по графику, с учётом "
                "допуска. Дни без графика и дни без прихода в отчёт "
                "не попадают: там опоздание не с чем сравнивать.",
            ),
            (
                "Отсутствие строки",
                "не означает «пришёл вовремя»: у человека мог не быть "
                "графика или он мог не выходить в этот день.",
            ),
        ],
    )


def _absences(actor: Actor, filters: dict, author: str) -> Sheet:
    """Отпуска, больничные и прочие отсутствия за период.

    Берётся та же очередь, что показывает раздел «Заявки», с теми же
    правилами области видимости и с тем же смыслом периода: фильтр идёт
    по датам САМОГО ОТСУТСТВИЯ, а не по дате подачи заявления. Оба
    значения есть в файле отдельными колонками, чтобы их нельзя было
    перепутать.
    """
    service = AbsenceService()
    office_id = filters.get("office_id")
    region_id = filters.get("region_id")
    first, last = _period(filters, fallback_single_day=False)

    queryset = service.queue(
        actor,
        office_id=office_id,
        region_id=region_id,
        date_from=first.isoformat(),
        date_to=last.isoformat(),
    ).order_by("requested_start_at", "id")

    def rows():
        for request in queryset.iterator(chunk_size=200):
            employee = request.employee
            yield [
                employee.employee_number,
                _full_name(employee),
                request.absence_type.name if request.absence_type else None,
                request.absence_type.code if request.absence_type else None,
                request.requested_start_at,
                request.requested_end_at,
                request.status,
                request.request_kind,
                request.submitted_at,
                request.reviewed_at,
                request.review_comment,
            ]

    return Sheet(
        title="Отсутствия",
        columns=["Табельный номер", "ФИО", "Вид отсутствия", "Код",
                 "Начало", "Окончание", "Статус", "Вид заявки",
                 "Подана", "Решение принято", "Комментарий решения"],
        rows=rows(),
        meta=base_meta(
            title="Отсутствия",
            author=author,
            period=(first, last),
            filters={"office_id": office_id, "region_id": region_id},
        )
        + [
            (
                "Период",
                "отобраны отсутствия, ПЕРЕСЕКАЮЩИЕСЯ с этими датами, "
                "а не поданные в эти даты. Дата подачи — отдельная "
                "колонка.",
            ),
            (
                "Статус",
                "состояние заявки, а не факт отсутствия: отклонённая и "
                "отменённая заявка тоже видна, чтобы отчёт не выглядел "
                "короче, чем очередь на экране.",
            ),
        ],
    )


def _presence_days(
    service: AttendanceHrService,
    actor: Actor,
    first: date,
    last: date,
    *,
    office_id,
    region_id,
    first_report=None,
):
    """Присутствие по дням периода: `(дата, отчёт сервиса)`.

    Один проход на два отчёта — «Посещаемость» за период и «Опоздания».
    Правила состояния живут в `AttendanceHrService`, и повторять их
    здесь было бы вторым источником правды о том же самом.
    """
    for day in days_in(first, last):
        if first_report is not None and day == first:
            yield day, first_report
            continue
        yield day, service.presence(
            actor, day=day, office_id=office_id, region_id=region_id
        )


def _period(filters: dict, *, fallback_single_day: bool) -> tuple[date, date]:
    """Период отчёта: разобранный, проверенный, с понятным умолчанием.

    Проверка стоит и здесь, и в сериализаторе заказа. Не дублирование:
    сюда приходят и запросы старого пути, отдающего файл сразу, — а
    пятилетний диапазон там держит соединение до таймаута шлюза вместо
    того, чтобы честно отказать.
    """
    single = filters.get("date")
    first = filters.get("date_from")
    last = filters.get("date_to")

    if first and last:
        pass
    elif single:
        first = last = single
    elif fallback_single_day:
        first = last = date.today()
    else:
        first, last = month_range(date.today())

    check_period(first, last)
    return first, last


def check_period(first: date, last: date) -> None:
    """Порядок дат и длина периода. Ошибка — на поле, а не в общий текст."""
    if last < first:
        raise ValidationFailed(
            "Конец периода раньше начала",
            details={"date_to": ["Дата окончания раньше даты начала"]},
        )
    span = (last - first).days + 1
    if span > MAX_PERIOD_DAYS:
        raise ValidationFailed(
            f"Период длиннее {MAX_PERIOD_DAYS} дней",
            details={
                "date_to": [
                    f"В один отчёт помещается не больше {MAX_PERIOD_DAYS} "
                    f"дней; выбрано {span}"
                ],
                "days": span,
                "limit": MAX_PERIOD_DAYS,
            },
        )


def _full_name(employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


__all__ = ["EXPORT_KINDS", "MAX_PERIOD_DAYS", "build_sheet", "check_period"]
