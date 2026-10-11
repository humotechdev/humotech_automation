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
from humotech.attendance.models import AttendanceEvent, AttendanceSession, FieldWorkRequest
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
from humotech.schedules.day_plan import plans_for

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
        now = django_timezone.now()

        # Состав — все, кто хоть один день периода числился в офисах выборки
        # и был трудоустроен, а не только те, кто числится в последний день.
        # Уволенный 15-го и переведённый 16-го остаются в своих днях (аудит
        # 12.10, B-04 / D-08). Каждый день человека относится к офису его
        # назначения в ЭТОТ день (`day_plan`).
        roster = self._roster(actor, office_ids, first=first, last=last, only=only_employee)
        employee_ids = list(roster)
        if not employee_ids:
            return _empty_report(scope_kind, scope_id, scope_name, first, last, tz, now)

        plans = plans_for(employee_ids, first, last)
        employed = self._employment(employee_ids)
        # Окно сессий с запасом в сутки: офисы выборки бывают в разных
        # поясах, а день сессии определяет пояс её офиса (C-06).
        start, end = range_bounds(first - timedelta(days=1), last + timedelta(days=1), tz)
        sessions = self._sessions(employee_ids, start, end, first, last)
        field_work = self._field_work(employee_ids, first, last)
        absences = self._absences(employee_ids, first, last, plans)
        rejected = self._rejected_scans(office_ids, *range_bounds(first, last, tz))

        return self._compute(
            scope_kind=scope_kind,
            scope_id=scope_id,
            scope_name=scope_name,
            first=first,
            last=last,
            tz=tz,
            generated_at=now,
            employee_ids=employee_ids,
            sessions=sessions,
            field_work=field_work,
            absences=absences,
            plans=plans,
            office_ids=set(office_ids),
            employed=employed,
            rejected=rejected,
        )

    def _roster(
        self,
        actor: Actor,
        office_ids: list[uuid.UUID],
        *,
        first: date,
        last: date,
        only: uuid.UUID | None,
    ) -> set[uuid.UUID]:
        """Кто хоть один день периода числился в офисах выборки.

        Уволенные в периоде — входят (их дни до увольнения — их дни).
        Приостановленные и архивные по-прежнему не входят: истории статуса
        нет, а как учитывать приостановку — вопрос владельцу (П-9, П-14).
        """
        queryset = EmployeeAssignment.objects.filter(
            is_primary=True,
            office_id__in=office_ids,
            valid_from__lte=last,
            employee__organization_id=actor.organization_id,
            employee__employment_status__in=("ACTIVE", "TERMINATED"),
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=first))
        if only:
            queryset = queryset.filter(employee_id=only)
        return set(queryset.values_list("employee_id", flat=True))

    @staticmethod
    def _employment(employee_ids) -> dict:
        """Период трудоустройства: `(дата приёма, дата увольнения | None)`."""
        return {
            row[0]: (row[1], row[2])
            for row in Employee.objects.filter(id__in=employee_ids).values_list(
                "id", "hire_date", "termination_date")
        }

    @staticmethod
    def _sessions(employee_ids, start, end, first, last) -> dict:
        """Сессии по дням. День — по поясу офиса сессии (C-06)."""
        grouped: dict[tuple[uuid.UUID, date], list[AttendanceSession]] = {}
        rows = (
            AttendanceSession.objects.filter(
                employee_id__in=employee_ids,
                started_at__gte=start,
                started_at__lt=end,
            )
            .exclude(status="INVALID")
            .select_related("office", "office__organization")
            .only("employee_id", "started_at", "ended_at", "duration_seconds", "status",
                  "office__timezone", "office__organization__default_timezone")
        )
        for row in rows:
            day = local_date(row.started_at, office_zone(row.office))
            if first <= day <= last:
                grouped.setdefault((row.employee_id, day), []).append(row)
        return grouped

    @staticmethod
    def _field_work(employee_ids, first, last) -> dict:
        """Confirmed off-site work is credited independently of physical visits."""
        return {
            (row.employee_id, row.date): row.norm_seconds
            for row in FieldWorkRequest.objects.filter(
                employee_id__in=employee_ids,
                date__gte=first, date__lte=last, status="CONFIRMED",
            ).only("employee_id", "date", "norm_seconds")
        }

    @staticmethod
    def _absences(employee_ids, first, last, plans) -> dict:
        result: dict[tuple[uuid.UUID, date], str] = {}
        zones = {
            employee_id: plans[(employee_id, first)].timezone
            for employee_id in employee_ids if (employee_id, first) in plans
        }
        start, end = range_bounds(first - timedelta(days=1), last + timedelta(days=1),
                                  next(iter(zones.values()), office_zone(None)))
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
            tz = zones.get(row.employee_id, office_zone(None))
            from_day = max(local_date(row.start_at, tz), first)
            to_day = min(local_date(row.end_at, tz), last)
            for day in days_in(from_day, to_day):
                result.setdefault((row.employee_id, day), code)
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
        field_work,
        absences,
        plans,
        office_ids,
        employed,
        rejected,
    ) -> Report:
        """Показатели по дням: каждый день человека — по плану ЭТОГО дня.

        * День считается, только если человек в этот день числился в офисе
          выборки и был трудоустроен (B-04 / D-08).
        * Будущий день и сегодняшний до конца смены — ещё не ожидаемый и
          не пропущенный; если человек уже пришёл, день засчитан (D-01).
        * Норма, начало смены и допуск — из `day_plan` (D-03, C-10).
        * Опоздание — в поясе офиса этого дня (C-06).
        """
        with_schedule: set = set()
        counted: set = set()

        expected_days = 0
        expected_seconds = 0
        attended_days = 0
        missed_days = 0
        sick_days = 0
        vacation_days = 0
        other_absence_days = 0
        worked_seconds = 0
        office_seconds = 0
        field_work_seconds = 0
        field_work_days = 0
        arrivals = 0
        on_time = 0
        late_count = 0
        late_minutes_total = 0
        open_sessions = 0
        overtime_seconds = 0
        undertime_seconds = 0
        days_without_norm = 0
        series: list[DayPoint] = []

        for day in days_in(first, last):
            day_worked = 0
            day_attended = 0
            day_expected = 0
            day_late = 0

            for employee_id in employee_ids:
                plan = plans.get((employee_id, day))
                if plan is None or plan.office_id not in office_ids:
                    continue
                hired, terminated = employed.get(employee_id, (None, None))
                if (hired and day < hired) or (terminated and day > terminated):
                    continue
                counted.add(employee_id)
                key = (employee_id, day)
                day_sessions = sessions.get(key, [])
                absence_code = absences.get(key)

                seconds = 0
                if day_sessions:
                    seconds = sum(s.duration_seconds or 0 for s in day_sessions)
                    worked_seconds += seconds
                    office_seconds += seconds
                    day_worked += seconds
                    open_sessions += sum(1 for s in day_sessions if s.ended_at is None)

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

                if not plan.has_schedule:
                    # Графика нет — норму посчитать нечем. Такой человек
                    # не попадает ни в числитель, ни в знаменатель, и
                    # `coverage` говорит, сколько таких.
                    continue
                with_schedule.add(employee_id)

                credited = 0
                if not day_sessions and key in field_work:
                    credited = field_work[key]

                settled = plan.is_settled(generated_at)
                if settled and plan.norm_seconds is not None:
                    deviation = seconds + credited - plan.norm_seconds
                    if deviation > 0:
                        overtime_seconds += deviation
                    elif deviation < 0:
                        undertime_seconds -= deviation

                if not plan.is_working:
                    continue  # выходной по графику или праздник
                future = day > local_date(generated_at, plan.timezone)
                if future and not day_sessions and not credited:
                    continue  # будущий день — не ожидаемый и не пропуск (D-01)

                expected_days += 1
                day_expected += 1
                if plan.norm_seconds is None:
                    days_without_norm += 1
                else:
                    expected_seconds += plan.norm_seconds

                if day_sessions:
                    attended_days += 1
                    day_attended += 1
                    arrivals += 1
                    if plan.starts_at is not None:
                        entry = min(s.started_at for s in day_sessions)
                        delta = int((entry - plan.starts_at).total_seconds() // 60)
                        late = max(delta - plan.late_grace_minutes, 0)
                        if late > 0:
                            late_count += 1
                            late_minutes_total += late
                            day_late += 1
                        else:
                            on_time += 1
                    else:
                        on_time += 1
                elif credited:
                    # One confirmed request credits exactly its approved daily
                    # norm; it is never an office arrival or QR session.
                    field_work_days += 1
                    field_work_seconds += credited
                    worked_seconds += credited
                    day_worked += credited
                    attended_days += 1
                    day_attended += 1
                elif settled:
                    missed_days += 1
                # Сегодня, смена ещё идёт: день ожидаемый (знаменатель явки
                # прежний — вопрос П-1), но пропуском он станет только после
                # конца смены (D-01).

            series.append(
                DayPoint(
                    day=day,
                    worked_seconds=day_worked,
                    attended=day_attended,
                    expected=day_expected,
                    late=day_late,
                )
            )

        employee_ids = [e for e in employee_ids if e in counted]

        totals = {
            "employees": len(employee_ids),
            "expected_working_days": expected_days,
            "expected_seconds": expected_seconds,
            "worked_seconds": worked_seconds,
            "office_seconds": office_seconds,
            "field_work_seconds": field_work_seconds,
            "field_work_days": field_work_days,
            "attended_days": attended_days,
            "missed_days": missed_days,
            "sick_leave_days": sick_days,
            "vacation_days": vacation_days,
            "other_absence_days": other_absence_days,
            "late_arrivals": late_count,
            "late_minutes_total": late_minutes_total,
            "open_sessions": open_sessions,
            "rejected_scans": rejected,
            # Отклонение от нормы — по каждому человеку и дню, а не разница
            # сумм по области: переработка одного не гасит недоработку
            # другого, люди без графика не участвуют (аудит 12.10, D-04).
            # Так же считает выгрузка «Рабочее время». Свёртка внутри
            # человека за период (суммированный учёт) — вопрос П-4.
            "overtime_seconds": overtime_seconds,
            "undertime_seconds": undertime_seconds,
            "deviation_rule": (
                "по каждому сотруднику и дню: факт минус норма графика; "
                "сверх нормы и недоработка суммируются отдельно; "
                "сотрудники без графика не участвуют"
            ),
            # Утверждённой переработки как процесса нет: положительное
            # отклонение — не утверждённая переработка (D-04).
            "approved_overtime_seconds": None,
            "days_without_norm": days_without_norm,
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
                    "дни с отметками или подтверждённой выездной работой / рабочие дни по графику. Дни "
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
                    "часы в офисе (перерывы не вычитаются: считается присутствие) "
                    "и плановая норма подтверждённой выездной работы / норма "
                    "часов по графику. Время в офисе и выездная работа также "
                    "представлены отдельно."
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
            "office_seconds": 0,
            "field_work_seconds": 0,
            "field_work_days": 0,
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
                formula="дни с отметками или подтверждённой выездной работой / рабочие дни по графику",
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
                formula="часы в офисе и на подтверждённой выездной работе / норма часов по графику",
                unit="hours",
            ),
        ],
        series=[],
    )


def _full_name(employee: Employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


__all__ = ["AnalyticsService", "Coverage", "DayPoint", "Ratio", "Report"]
