"""Показатели посещаемости: числа, из которых видно, как они получены.

Каждый процент здесь обязан приходить вместе с числителем, знаменателем
и словесным определением формулы. Причина простая: процент без них
невозможно проверить и не с чем сопоставить. «Посещаемость 95,6%»
не отвечает ни на один вопрос, который задаст кадровик, а «181 из 189
рабочих дней» отвечает сразу на все.

**Нулевой знаменатель даёт `null`, а не ноль.** У офиса, где в выбранном
периоде не было ни одного рабочего дня, посещаемость не «0%» — её просто
нет. Ноль в этой клетке читается как «никто не приходил» и попадает
в отчёт, по которому принимают решения о людях.

**Присутствие — это присутствие.** Ни один показатель здесь не называется
эффективностью или продуктивностью. Система измеряет, кто когда был
в офисе; о том, как человек работал, она не знает ничего, и делать вид,
что знает, нельзя.

**Покрытие данных называется вслух.** Норму рабочего времени можно
посчитать только тем, у кого есть график. Если график есть у половины
штата, показатель считается по этой половине — и `coverage` говорит, по
какой именно. Молчаливый расчёт «по тем, кто нашёлся» превращает
неполноту данных в уверенное число.

Сессия принадлежит дню своего начала — то же правило, что и везде
в проекте (см. `attendance/statistics.py`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q

from humotech.absences.models import EmployeeAbsence
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.attendance.statistics import (
    COUNTED_ABSENCE_STATUSES,
    SICK_CODES,
    VACATION_CODES,
)
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor
from humotech.core.service import BaseService
from humotech.core.timeframes import days_in, local_date, office_zone, range_bounds
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.services import (
    current_primary_assignment_filter,
    roster_assignment_filter,
)
from humotech.offices.models import Office
from humotech.schedules.models import CalendarException, EmployeeScheduleAssignment

# Больше года за один запрос не считается: это не ограничение расчёта,
# а защита от случайного «с 1970 года» в параметрах.
MAX_PERIOD_DAYS = 366


@dataclass(frozen=True)
class Ratio:
    """Доля с полным раскрытием того, как она получена."""

    key: str
    title: str
    numerator: float
    denominator: float
    #: Словами: что именно делится на что. Попадает в ответ и в отчёт.
    formula: str
    #: `days`, `hours` или `people` — чтобы клиент не гадал по названию.
    unit: str

    @property
    def percent(self) -> float | None:
        """Ноль в знаменателе — это `null`, а не ноль процентов.

        Ноль процентов утверждает, что никто не приходил. Отсутствие
        рабочих дней в периоде такого не утверждает — оно вообще ничего
        не утверждает о посещаемости.
        """
        if not self.denominator:
            return None
        return round(self.numerator * 100 / self.denominator, 1)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "percent": self.percent,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "formula": self.formula,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class Coverage:
    """По скольким людям вообще можно было посчитать норму."""

    employees_total: int
    employees_with_schedule: int

    @property
    def ratio(self) -> float | None:
        if not self.employees_total:
            return None
        return round(self.employees_with_schedule * 100 / self.employees_total, 1)

    def as_dict(self) -> dict:
        return {
            "employees_total": self.employees_total,
            "employees_with_schedule": self.employees_with_schedule,
            "percent": self.ratio,
            "note": (
                "Норма рабочего времени считается только по сотрудникам "
                "с назначенным графиком. Остальные учтены в численности, "
                "но не в знаменателях."
            ),
        }


@dataclass(frozen=True)
class DayPoint:
    """Точка временного ряда — для графика по дням."""

    day: date
    worked_seconds: int
    attended: int
    expected: int
    late: int


@dataclass(frozen=True)
class Report:
    scope_kind: str
    scope_id: uuid.UUID | None
    scope_name: str
    first: date
    last: date
    timezone: str
    generated_at: datetime

    headcount: int
    coverage: Coverage
    totals: dict
    ratios: list[Ratio]
    series: list[DayPoint] = field(default_factory=list)

    def as_dict(self, *, with_series: bool = True) -> dict:
        body = {
            "scope": {
                "kind": self.scope_kind,
                "id": str(self.scope_id) if self.scope_id else None,
                "name": self.scope_name,
            },
            "period": {
                "first": self.first.isoformat(),
                "last": self.last.isoformat(),
                "timezone": self.timezone,
            },
            # Когда посчитано. Для периода, включающего сегодня, число
            # ещё вырастет — и клиент должен об этом знать.
            "generated_at": self.generated_at.isoformat(),
            "headcount": self.headcount,
            "coverage": self.coverage.as_dict(),
            "totals": self.totals,
            "ratios": [ratio.as_dict() for ratio in self.ratios],
        }
        if with_series:
            body["series"] = [
                {
                    "day": point.day.isoformat(),
                    "worked_seconds": point.worked_seconds,
                    "attended": point.attended,
                    "expected": point.expected,
                    "late": point.late,
                }
                for point in self.series
            ]
        return body


class AnalyticsService(BaseService):
    """Сводные показатели. Требует `analytics.read`.

    Показатели по конкретному сотруднику закрыты отдельным разрешением
    `employees.read`: сводка по офису обезличена, а строка по человеку —
    это уже наблюдение за конкретным работником, и право на него другое.
    """

    def organization(
        self, actor: Actor, *, first: date, last: date
    ) -> Report:
        self.access.require(actor, "analytics.read")
        offices = self._offices(actor)
        return self._build(
            actor,
            offices=offices,
            first=first,
            last=last,
            scope_kind="organization",
            scope_id=actor.organization_id,
            scope_name="Организация",
        )

    def region(
        self, actor: Actor, region_id: uuid.UUID, *, first: date, last: date
    ) -> Report:
        self.access.require(actor, "analytics.read")
        region = self.access.require_region(actor, region_id)
        offices = self._offices(actor, region_id=region_id)
        return self._build(
            actor,
            offices=offices,
            first=first,
            last=last,
            scope_kind="region",
            scope_id=region_id,
            scope_name=region.name,
        )

    def office(
        self, actor: Actor, office_id: uuid.UUID, *, first: date, last: date
    ) -> Report:
        self.access.require(actor, "analytics.read")
        office = self.access.require_office(actor, office_id)
        return self._build(
            actor,
            offices=[office],
            first=first,
            last=last,
            scope_kind="office",
            scope_id=office_id,
            scope_name=office.name,
        )

    def employee(
        self, actor: Actor, employee_id: uuid.UUID, *, first: date, last: date
    ) -> Report:
        """Показатели одного человека.

        Требует ДВА разрешения. Сводка по офису обезличена и права на неё
        достаточно `analytics.read`; строка по конкретному человеку —
        это наблюдение за работником, и его открывает `employees.read`.
        """
        self.access.require(actor, "analytics.read")
        self.access.require(actor, "employees.read")

        person = Employee.objects.filter(
            id=employee_id, organization_id=actor.organization_id
        ).first()
        if person is None:
            from humotech.core.errors import NotFound

            raise NotFound("Сотрудник не найден")

        assignment = (
            EmployeeAssignment.objects.filter(
                current_primary_assignment_filter(last), employee_id=employee_id
            )
            .select_related("office")
            .first()
        )
        if assignment is None or assignment.office is None:
            from humotech.core.errors import NotFound

            raise NotFound("Сотрудник не найден")
        self.access.require_office(actor, assignment.office_id)

        return self._build(
            actor,
            offices=[assignment.office],
            first=first,
            last=last,
            scope_kind="employee",
            scope_id=employee_id,
            scope_name=_full_name(person),
            only_employee=employee_id,
        )

    # -------------------------------------------------------------- сравнение

    def compare(
        self,
        actor: Actor,
        *,
        kind: str,
        left_id: uuid.UUID | None,
        right_id: uuid.UUID | None,
        first: date,
        last: date,
        right_first: date | None = None,
        right_last: date | None = None,
    ) -> dict:
        """Сравнение двух областей или двух периодов.

        Возвращаются обе стороны ЦЕЛИКОМ, а не только разница: увидев
        «+12%», кадровик первым делом спросит «двенадцать от чего», и
        ответ должен быть в том же ответе сервера.
        """
        if kind not in ("region", "office", "period"):
            raise ValidationFailed(
                "Сравнивать можно регионы, офисы или периоды",
                details={"kind": kind, "allowed": ["region", "office", "period"]},
            )

        if kind == "period":
            if not right_first or not right_last:
                raise ValidationFailed(
                    "Для сравнения периодов нужен второй период",
                    details={"fields": ["right_first", "right_last"]},
                )
            left = self.organization(actor, first=first, last=last)
            right = self.organization(actor, first=right_first, last=right_last)
        elif kind == "region":
            left = self.region(actor, left_id, first=first, last=last)
            right = self.region(actor, right_id, first=first, last=last)
        else:
            left = self.office(actor, left_id, first=first, last=last)
            right = self.office(actor, right_id, first=first, last=last)

        return {
            "kind": kind,
            "left": left.as_dict(),
            "right": right.as_dict(),
            "differences": _differences(left, right),
        }

    # ------------------------------------------------------------ внутреннее

    def _offices(
        self, actor: Actor, *, region_id: uuid.UUID | None = None
    ) -> list[Office]:
        queryset = Office.objects.filter(organization_id=actor.organization_id)
        visible = self.access.office_filter(actor)
        if visible is not None:
            queryset = queryset.filter(visible)
        if region_id:
            queryset = queryset.filter(region_id=region_id)
        # Регион подтягивается сразу: обзор группирует офисы по регионам,
        # и без этого получился бы запрос на каждый офис.
        return list(queryset.select_related("region").order_by("name"))

    def _build(
        self,
        actor: Actor,
        *,
        offices: list[Office],
        first: date,
        last: date,
        scope_kind: str,
        scope_id: uuid.UUID | None,
        scope_name: str,
        only_employee: uuid.UUID | None = None,
    ) -> Report:
        _validate_period(first, last)
        tz = office_zone(offices[0]) if offices else office_zone(None)
        from django.utils import timezone as django_timezone

        if not offices:
            return _empty_report(
                scope_kind, scope_id, scope_name, first, last, tz,
                django_timezone.now(),
            )

        office_ids = [office.id for office in offices]
        start, end = range_bounds(first, last, tz)

        roster = self._roster(actor, office_ids, at=last, only=only_employee)
        employee_ids = list(roster)
        if not employee_ids:
            return _empty_report(
                scope_kind, scope_id, scope_name, first, last, tz,
                django_timezone.now(),
            )

        # Пять запросов на весь период любой длины: помесячный отчёт стоит
        # столько же, сколько дневной. Наивный расчёт «день за днём» дал бы
        # тридцать раз по пять.
        sessions = self._sessions(employee_ids, start, end, tz)
        absences = self._absences(employee_ids, first, last, tz)
        schedules = self._schedules(employee_ids, first, last)
        exceptions = self._exceptions(actor, office_ids, first, last)
        rejected = self._rejected_scans(office_ids, start, end)

        return self._compute(
            scope_kind=scope_kind,
            scope_id=scope_id,
            scope_name=scope_name,
            first=first,
            last=last,
            tz=tz,
            generated_at=django_timezone.now(),
            employee_ids=employee_ids,
            sessions=sessions,
            absences=absences,
            schedules=schedules,
            exceptions=exceptions,
            rejected=rejected,
        )

    def _roster(
        self,
        actor: Actor,
        office_ids: list[uuid.UUID],
        *,
        at: date,
        only: uuid.UUID | None,
    ) -> set[uuid.UUID]:
        queryset = EmployeeAssignment.objects.filter(
            roster_assignment_filter(at),
            office_id__in=office_ids,
            employee__organization_id=actor.organization_id,
        )
        if only:
            queryset = queryset.filter(employee_id=only)
        return set(queryset.values_list("employee_id", flat=True))

    @staticmethod
    def _sessions(employee_ids, start, end, tz) -> dict:
        grouped: dict[tuple[uuid.UUID, date], list[AttendanceSession]] = {}
        rows = (
            AttendanceSession.objects.filter(
                employee_id__in=employee_ids,
                started_at__gte=start,
                started_at__lt=end,
            )
            .exclude(status="INVALID")
            .only("employee_id", "started_at", "ended_at", "duration_seconds")
        )
        for row in rows:
            day = local_date(row.started_at, tz)
            grouped.setdefault((row.employee_id, day), []).append(row)
        return grouped

    @staticmethod
    def _absences(employee_ids, first, last, tz) -> dict:
        result: dict[tuple[uuid.UUID, date], str] = {}
        start, end = range_bounds(first, last, tz)
        rows = (
            EmployeeAbsence.objects.filter(
                employee_id__in=employee_ids,
                status__in=COUNTED_ABSENCE_STATUSES,
                start_at__lt=end,
                end_at__gte=start,
            )
            .select_related("absence_type")
            .order_by("start_at")
        )
        for row in rows:
            code = row.absence_type.code if row.absence_type_id else ""
            from_day = max(local_date(row.start_at, tz), first)
            to_day = min(local_date(row.end_at, tz), last)
            for day in days_in(from_day, to_day):
                result.setdefault((row.employee_id, day), code)
        return result

    @staticmethod
    def _schedules(employee_ids, first, last) -> dict:
        """Действующие графики с днями — одним запросом на весь период."""
        rows = (
            EmployeeScheduleAssignment.objects.filter(
                employee_id__in=employee_ids, valid_from__lte=last
            )
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=first))
            .select_related("schedule")
            .prefetch_related("schedule__days")
            .order_by("employee_id", "valid_from")
        )
        result: dict[uuid.UUID, list] = {}
        for row in rows:
            result.setdefault(row.employee_id, []).append(row)
        return result

    def _exceptions(self, actor, office_ids, first, last) -> dict:
        result: dict[date, bool] = {}
        rows = CalendarException.objects.filter(
            Q(office_id__in=office_ids) | Q(office__isnull=True),
            organization_id=actor.organization_id,
            date__gte=first,
            date__lte=last,
            is_active=True,
        )
        for row in sorted(rows, key=lambda r: r.office_id is not None):
            result[row.date] = row.is_working_day
        return result

    @staticmethod
    def _rejected_scans(office_ids, start, end) -> int:
        return AttendanceEvent.objects.filter(
            office_id__in=office_ids,
            occurred_at__gte=start,
            occurred_at__lt=end,
            verification_status="REJECTED",
        ).count()

    @staticmethod
    def _compute(
        *,
        scope_kind,
        scope_id,
        scope_name,
        first,
        last,
        tz,
        generated_at,
        employee_ids,
        sessions,
        absences,
        schedules,
        exceptions,
        rejected,
    ) -> Report:
        with_schedule = {eid for eid in employee_ids if schedules.get(eid)}

        expected_days = 0
        expected_seconds = 0
        attended_days = 0
        missed_days = 0
        sick_days = 0
        vacation_days = 0
        other_absence_days = 0
        worked_seconds = 0
        arrivals = 0
        on_time = 0
        late_count = 0
        late_minutes_total = 0
        open_sessions = 0
        series: list[DayPoint] = []

        for day in days_in(first, last):
            day_worked = 0
            day_attended = 0
            day_expected = 0
            day_late = 0

            for employee_id in employee_ids:
                key = (employee_id, day)
                day_sessions = sessions.get(key, [])
                absence_code = absences.get(key)

                if day_sessions:
                    seconds = sum(s.duration_seconds or 0 for s in day_sessions)
                    worked_seconds += seconds
                    day_worked += seconds
                    open_sessions += sum(
                        1 for s in day_sessions if s.ended_at is None
                    )

                if absence_code is not None:
                    if absence_code in SICK_CODES:
                        sick_days += 1
                    elif absence_code in VACATION_CODES:
                        vacation_days += 1
                    else:
                        other_absence_days += 1
                    # День отсутствия не считается ни рабочим, ни пропущенным:
                    # человек законно не должен был приходить.
                    continue

                plan = _plan_for(schedules.get(employee_id), day, exceptions)
                if plan is None:
                    # Графика нет — норму посчитать нечем. Такой человек
                    # не попадает ни в числитель, ни в знаменатель, и
                    # `coverage` говорит, сколько таких.
                    continue

                start_time, seconds_norm = plan
                if start_time is None:
                    continue  # нерабочий день по графику или праздник

                expected_days += 1
                expected_seconds += seconds_norm
                day_expected += 1

                if day_sessions:
                    attended_days += 1
                    day_attended += 1
                    arrivals += 1
                    entry = min(s.started_at for s in day_sessions)
                    planned = datetime.combine(day, start_time, tzinfo=tz)
                    delta = int(
                        (entry.astimezone(tz) - planned).total_seconds() // 60
                    )
                    grace = _grace_for(schedules.get(employee_id), day)
                    late = max(delta - grace, 0)
                    if late > 0:
                        late_count += 1
                        late_minutes_total += late
                        day_late += 1
                    else:
                        on_time += 1
                else:
                    missed_days += 1

            series.append(
                DayPoint(
                    day=day,
                    worked_seconds=day_worked,
                    attended=day_attended,
                    expected=day_expected,
                    late=day_late,
                )
            )

        totals = {
            "employees": len(employee_ids),
            "expected_working_days": expected_days,
            "expected_seconds": expected_seconds,
            "worked_seconds": worked_seconds,
            "attended_days": attended_days,
            "missed_days": missed_days,
            "sick_leave_days": sick_days,
            "vacation_days": vacation_days,
            "other_absence_days": other_absence_days,
            "late_arrivals": late_count,
            "late_minutes_total": late_minutes_total,
            "open_sessions": open_sessions,
            "rejected_scans": rejected,
            # Переработка и недоработка — две стороны одной разницы, и
            # обе положительные: «-12 часов переработки» читать неудобно.
            "overtime_seconds": max(worked_seconds - expected_seconds, 0),
            "undertime_seconds": max(expected_seconds - worked_seconds, 0),
            "average_seconds_per_attended_day": (
                round(worked_seconds / attended_days) if attended_days else None
            ),
        }

        ratios = [
            Ratio(
                key="attendance",
                title="Посещаемость",
                numerator=attended_days,
                denominator=expected_days,
                formula=(
                    "дни с отметками / рабочие дни по графику. Дни "
                    "оформленного отсутствия исключены из обеих частей; "
                    "сотрудники без графика не участвуют."
                ),
                unit="days",
            ),
            Ratio(
                key="punctuality",
                title="Приход вовремя",
                numerator=on_time,
                denominator=arrivals,
                formula=(
                    "приходы не позже начала смены с учётом допуска "
                    "late_grace_minutes / все приходы в рабочие дни."
                ),
                unit="days",
            ),
            Ratio(
                key="worked_vs_expected",
                title="Отработано от нормы",
                numerator=round(worked_seconds / 3600, 2),
                denominator=round(expected_seconds / 3600, 2),
                formula=(
                    "часы в офисе / норма часов по графику. Перерывы "
                    "не вычитаются: считается присутствие, а не "
                    "оплачиваемое время."
                ),
                unit="hours",
            ),
        ]

        return Report(
            scope_kind=scope_kind,
            scope_id=scope_id,
            scope_name=scope_name,
            first=first,
            last=last,
            timezone=str(tz),
            generated_at=generated_at,
            headcount=len(employee_ids),
            coverage=Coverage(
                employees_total=len(employee_ids),
                employees_with_schedule=len(with_schedule),
            ),
            totals=totals,
            ratios=ratios,
            series=series,
        )


# --- вспомогательное ---------------------------------------------------------


def _plan_for(assignments, day: date, exceptions: dict) -> tuple | None:
    """Начало смены и норма секунд на день. `None` — графика нет вовсе."""
    if not assignments:
        return None
    schedule = None
    for assignment in assignments:
        if assignment.valid_from <= day and (
            assignment.valid_to is None or assignment.valid_to >= day
        ):
            schedule = assignment.schedule
    if schedule is None:
        return None

    weekday = day.isoweekday()
    match = next((d for d in schedule.days.all() if d.weekday == weekday), None)
    working = bool(match and match.is_working_day)
    if day in exceptions:
        working = exceptions[day]
    if not working or match is None or match.start_time is None:
        return (None, 0)

    end = match.end_time or match.start_time
    norm = _seconds_between(match.start_time, end)
    return (match.start_time, norm)


def _grace_for(assignments, day: date) -> int:
    if not assignments:
        return 0
    for assignment in assignments:
        if assignment.valid_from <= day and (
            assignment.valid_to is None or assignment.valid_to >= day
        ):
            return assignment.schedule.late_grace_minutes or 0
    return 0


def _seconds_between(start: time, end: time) -> int:
    """Длина смены; переход через полночь считается как ночная смена."""
    base = date(2000, 1, 1)
    first = datetime.combine(base, start)
    second = datetime.combine(base, end)
    if second <= first:
        second += timedelta(days=1)
    return int((second - first).total_seconds())


def _differences(left: Report, right: Report) -> list[dict]:
    """Разница по каждой доле — в процентных пунктах и в самих числах.

    Разница двух процентов — это процентные ПУНКТЫ, а не проценты.
    Назвать 95% против 90% «разницей в 5%» значит сказать неправду:
    5% от 90 — это 4,5, а не 5.
    """
    right_by_key = {ratio.key: ratio for ratio in right.ratios}
    result = []
    for ratio in left.ratios:
        other = right_by_key.get(ratio.key)
        if other is None:
            continue
        both_known = ratio.percent is not None and other.percent is not None
        result.append(
            {
                "key": ratio.key,
                "title": ratio.title,
                "left": ratio.as_dict(),
                "right": other.as_dict(),
                "difference_points": (
                    round(ratio.percent - other.percent, 1) if both_known else None
                ),
                "difference_numerator": ratio.numerator - other.numerator,
                "difference_denominator": ratio.denominator - other.denominator,
                # null означает «сравнивать не с чем»: у одной из сторон
                # знаменатель нулевой, и разница не определена.
                "comparable": both_known,
            }
        )
    return result


#: Границы допустимых дат в параметрах отчётов и аналитики.
#:
#: Не бизнес-правило, а защита арифметики: 0001-01-01 и 9999-12-31 —
#: законные даты ISO, но «сутки до» первой и «сутки после» второй
#: в Python не существуют, и сервис отвечал OverflowError, то есть 500.
#: Сравнение «с тем же периодом год назад» отступает ещё на год, поэтому
#: запас с обеих сторон.
DATE_MIN = date(1900, 1, 1)
DATE_MAX = date(2200, 12, 31)


def check_date(value: date | None, field: str) -> date | None:
    """Дата из запроса — в пределах, где с ней можно считать."""
    if value is not None and not (DATE_MIN <= value <= DATE_MAX):
        raise ValidationFailed(
            f"Дата вне допустимого диапазона {DATE_MIN.isoformat()} — "
            f"{DATE_MAX.isoformat()}",
            details={field: [f"Допустимы даты с {DATE_MIN.isoformat()} "
                             f"по {DATE_MAX.isoformat()}"]},
        )
    return value


def _validate_period(first: date, last: date) -> None:
    check_date(first, "date_from")
    check_date(last, "date_to")
    if last < first:
        raise ValidationFailed(
            "Конец периода раньше начала",
            details={"first": first.isoformat(), "last": last.isoformat()},
        )
    if (last - first).days + 1 > MAX_PERIOD_DAYS:
        raise ValidationFailed(
            f"Период не длиннее {MAX_PERIOD_DAYS} дней",
            details={"days": (last - first).days + 1},
        )


def _empty_report(
    scope_kind, scope_id, scope_name, first, last, tz, generated_at
) -> Report:
    """Ни одного сотрудника в области — все доли неопределены.

    Именно неопределены, а не равны нулю: нулевая посещаемость офиса,
    в котором никто не числится, — это утверждение, которого никто
    не делал.
    """
    return Report(
        scope_kind=scope_kind,
        scope_id=scope_id,
        scope_name=scope_name,
        first=first,
        last=last,
        timezone=str(tz),
        generated_at=generated_at,
        headcount=0,
        coverage=Coverage(employees_total=0, employees_with_schedule=0),
        totals={
            "employees": 0,
            "expected_working_days": 0,
            "expected_seconds": 0,
            "worked_seconds": 0,
            "attended_days": 0,
            "missed_days": 0,
            "sick_leave_days": 0,
            "vacation_days": 0,
            "other_absence_days": 0,
            "late_arrivals": 0,
            "late_minutes_total": 0,
            "open_sessions": 0,
            "rejected_scans": 0,
            "overtime_seconds": 0,
            "undertime_seconds": 0,
            "average_seconds_per_attended_day": None,
        },
        ratios=[
            Ratio(
                key="attendance",
                title="Посещаемость",
                numerator=0,
                denominator=0,
                formula="дни с отметками / рабочие дни по графику",
                unit="days",
            ),
            Ratio(
                key="punctuality",
                title="Приход вовремя",
                numerator=0,
                denominator=0,
                formula="приходы вовремя / все приходы",
                unit="days",
            ),
            Ratio(
                key="worked_vs_expected",
                title="Отработано от нормы",
                numerator=0,
                denominator=0,
                formula="часы в офисе / норма часов по графику",
                unit="hours",
            ),
        ],
        series=[],
    )


def _full_name(employee: Employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


__all__ = ["AnalyticsService", "Coverage", "DayPoint", "Ratio", "Report"]
