"""Обзор аналитики одним ответом: всё, что показывает страница «Аналитика».

Правила те же, что у `metrics.py`, и взяты оттуда же, а не повторены:
план дня (`_plan_for`), допуск опоздания (`_grace_for`), виды отсутствий,
область видимости. Второй реализации «посещаемости для страницы» нет —
иначе цифра на странице однажды разошлась бы с отчётом.

Что добавлено сверх `metrics.py` и почему:

**Пояс каждого офиса.** Сутки режутся по поясу офиса сотрудника, а не по
поясу первого офиса выборки. Сессия в 00:30 по Ташкенту — это новый день
в Ташкенте, даже если в выборке есть офис в другом поясе.

**Будущие дни — не неявка.** Период может заканчиваться сегодня или
позже; день, который ещё не наступил, в знаменатель не входит.

**Среднее время — только по закрытым посещениям.** День, в котором есть
незакрытая сессия, в среднее не входит: у неё нет конца, и её длительность
неокончательна.

**Ритм прихода — относительно личной смены.** Сдвиг первого входа
считается от начала смены самого сотрудника: у человека со сменой в 10:00
приход в 10:05 — это пять минут после начала, а не час опоздания.
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter
from datetime import date, datetime, timedelta

from django.db.models import Q
from django.utils import timezone as django_timezone

from humotech.absences.models import EmployeeAbsence
from humotech.analytics.metrics import (
    AnalyticsService,
    _grace_for,
    _plan_for,
    _validate_period,
)
from humotech.attendance.models import AttendanceSession
from humotech.attendance.statistics import (
    COUNTED_ABSENCE_STATUSES,
    SICK_CODES,
    VACATION_CODES,
)
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor
from humotech.core.timeframes import days_in, local_date, office_zone, range_bounds
from humotech.employees.models import EmployeeAssignment
from humotech.employees.services import roster_assignment_filter
from humotech.offices.models import Office
from humotech.schedules.models import CalendarException

#: Ширина столбца гистограммы прихода, минуты.
BUCKET_MINUTES = 10
#: Границы гистограммы относительно начала смены. Всё раньше — в первом
#: столбце, всё позже — в последнем: хвосты не растягивают ось.
EARLIEST = -60
LATEST = 90


def _ratio(numerator: float, denominator: float) -> dict:
    """Доля с числителем и знаменателем. Нулевой знаменатель — `null`."""
    return {
        "numerator": numerator,
        "denominator": denominator,
        "percent": round(numerator * 100 / denominator, 1) if denominator else None,
    }


def _points(left: dict, right: dict) -> float | None:
    """Разница двух долей в процентных ПУНКТАХ. Неизвестная сторона — `null`."""
    if left["percent"] is None or right["percent"] is None:
        return None
    return round(left["percent"] - right["percent"], 1)


class OverviewService(AnalyticsService):
    """Обзор для страницы «Аналитика». Требует `analytics.read`."""

    def overview(
        self,
        actor: Actor,
        *,
        first: date,
        last: date,
        region_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        # Отдел и сотрудник сужают состав, а не пересчитывают правила:
        # явка одного человека считается тем же способом, что и явка
        # компании, — иначе цифра на его карточке разошлась бы с общей.
        department_id: uuid.UUID | None = None,
        employee_id: uuid.UUID | None = None,
        weekday: int | None = None,
        now: datetime | None = None,
    ) -> dict:
        self.access.require(actor, "analytics.read")
        _validate_period(first, last)
        if weekday is not None and weekday not in range(1, 8):
            raise ValidationFailed(
                "День недели — число от 1 (понедельник) до 7",
                details={"weekday": weekday},
            )

        if office_id:
            offices = [self.access.require_office(actor, office_id)]
        else:
            if region_id:
                self.access.require_region(actor, region_id)
            offices = self._offices(actor, region_id=region_id)

        moment = now or django_timezone.now()
        length = (last - first).days + 1
        previous_last = first - timedelta(days=1)
        previous_first = previous_last - timedelta(days=length - 1)

        narrow = {"department_id": department_id, "employee_id": employee_id}
        current = self._collect(actor, offices, first, last, weekday, moment, **narrow)
        previous = self._collect(
            actor, offices, previous_first, previous_last, weekday, moment, **narrow
        )

        attendance = _ratio(current["attended"], current["expected"])
        previous_attendance = _ratio(previous["attended"], previous["expected"])

        return {
            "period": {"first": first.isoformat(), "last": last.isoformat(), "days": length},
            "previous_period": {
                "first": previous_first.isoformat(),
                "last": previous_last.isoformat(),
            },
            "weekday": weekday,
            "generated_at": moment.isoformat(),
            "timezones": sorted({str(office_zone(office)) for office in offices}),
            "summary": {
                "attendance": attendance,
                "previous_attendance": previous_attendance,
                "difference_points": _points(attendance, previous_attendance),
                "on_time": _ratio(current["on_time"], current["arrivals"]),
                "late": _ratio(current["late"], current["arrivals"]),
                "average_seconds": (
                    round(current["closed_seconds"] / current["closed_days"])
                    if current["closed_days"] else None
                ),
                "open_sessions": current["open_sessions"],
                "missed_days": current["missed"],
                "vacation_days": current["vacation"],
                "sick_leave_days": current["sick"],
                "other_absence_days": current["other"],
            },
            "days": current["days"],
            "previous_days": [
                {
                    "day": one["day"],
                    "attended": one["attended"],
                    "expected": one["expected"],
                    "percent": one["percent"],
                }
                for one in previous["days"]
            ],
            "offices": self._ranking(offices, current, previous),
            # Регионы — та же явка, собранная на уровень выше. Отдельным
            # запросом её не считают: складывать офисы дважды по-разному
            # значит однажды получить два разных числа про одно и то же.
            "regions": self._regions(offices, current, previous),
            # Рейтинг людей: по явке и по опозданиям. Нужен, чтобы
            # увидеть не «средняя по компании 92 %», а кто именно эти
            # восемь процентов.
            "employees": self._people(current),
            "arrivals": self._arrivals(current),
            "weekdays": self._weekdays(current),
        }

    # ------------------------------------------------------------ сбор данных

    def _collect(
        self,
        actor: Actor,
        offices: list[Office],
        first: date,
        last: date,
        weekday: int | None,
        moment: datetime,
        department_id: uuid.UUID | None = None,
        employee_id: uuid.UUID | None = None,
    ) -> dict:
        state = _empty_state(offices)
        if not offices:
            state["days"] = [_day_row(day, {}, weekday, future=False) for day in days_in(first, last)]
            return state

        zones = {office.id: office_zone(office) for office in offices}
        people = EmployeeAssignment.objects.filter(
            roster_assignment_filter(last),
            office_id__in=list(zones),
            employee__organization_id=actor.organization_id,
        )
        if department_id is not None:
            people = people.filter(department_id=department_id)
        if employee_id is not None:
            people = people.filter(employee_id=employee_id)
        roster = dict(people.values_list("employee_id", "office_id"))
        employee_ids = list(roster)

        # Границы периода — самые широкие по всем поясам выборки; день
        # каждой сессии дальше определяется в поясе её офиса.
        bounds = [range_bounds(first, last, tz) for tz in set(zones.values())]
        start = min(one[0] for one in bounds)
        end = max(one[1] for one in bounds)

        sessions: dict[tuple, list[AttendanceSession]] = {}
        absences: dict[tuple, str] = {}
        schedules: dict = {}
        if employee_ids:
            for row in (
                AttendanceSession.objects.filter(
                    employee_id__in=employee_ids, started_at__gte=start, started_at__lt=end
                )
                .exclude(status="INVALID")
                .only("employee_id", "started_at", "ended_at", "duration_seconds")
            ):
                tz = zones[roster[row.employee_id]]
                day = local_date(row.started_at, tz)
                if first <= day <= last:
                    sessions.setdefault((row.employee_id, day), []).append(row)

            for row in (
                EmployeeAbsence.objects.filter(
                    employee_id__in=employee_ids,
                    status__in=COUNTED_ABSENCE_STATUSES,
                    start_at__lt=end,
                    end_at__gte=start,
                )
                .select_related("absence_type")
                .order_by("start_at")
            ):
                tz = zones[roster[row.employee_id]]
                code = row.absence_type.code if row.absence_type_id else ""
                from_day = max(local_date(row.start_at, tz), first)
                to_day = min(local_date(row.end_at, tz), last)
                for day in days_in(from_day, to_day):
                    absences.setdefault((row.employee_id, day), code)

            schedules = self._schedules(employee_ids, first, last)

        exceptions = _exceptions(actor, list(zones), first, last)
        today = {office_id: local_date(moment, tz) for office_id, tz in zones.items()}

        by_day: dict[date, dict] = {}
        for employee_id, office_id in roster.items():
            tz = zones[office_id]
            for day in days_in(first, last):
                if day > today[office_id]:
                    continue  # день ещё не наступил — это не неявка
                if weekday is not None and day.isoweekday() != weekday:
                    continue
                counts = by_day.setdefault(day, _day_counts())
                key = (employee_id, day)
                day_sessions = sessions.get(key, [])

                code = absences.get(key)
                if code is not None:
                    bucket = "sick" if code in SICK_CODES else "vacation" if code in VACATION_CODES else "other"
                    counts[bucket] += 1
                    state[bucket] += 1
                    continue

                plan = _plan_for(schedules.get(employee_id), day, exceptions.get(office_id, {}))
                if plan is None or plan[0] is None:
                    continue  # нет графика или нерабочий день — не неявка

                start_time = plan[0]
                counts["expected"] += 1
                state["expected"] += 1
                state["office_expected"][office_id] += 1
                state["person_expected"][employee_id] += 1
                state["person_office"][employee_id] = office_id
                week = state["weekdays"].setdefault(day.isoweekday(), _week_counts())
                week["expected"] += 1

                if not day_sessions:
                    counts["missed"] += 1
                    state["missed"] += 1
                    continue

                counts["attended"] += 1
                state["attended"] += 1
                state["office_attended"][office_id] += 1
                state["person_attended"][employee_id] += 1
                week["attended"] += 1
                state["arrivals"] += 1
                week["arrivals"] += 1

                entry = min(one.started_at for one in day_sessions).astimezone(tz)
                planned = datetime.combine(day, start_time, tzinfo=tz)
                delta = int((entry - planned).total_seconds() // 60)
                grace = _grace_for(schedules.get(employee_id), day)
                state["starts"][start_time.strftime("%H:%M")] += 1
                state["entries"].append(entry.hour * 60 + entry.minute)

                index = (min(max(delta, EARLIEST), LATEST - 1) - EARLIEST) // BUCKET_MINUTES
                column = state["histogram"][index]
                if delta > grace:
                    counts["late"] += 1
                    state["late"] += 1
                    state["person_late"][employee_id] += 1
                    # Минуты сверх допуска, а не вся разница: организация,
                    # разрешившая приходить на четверть часа позже,
                    # считает опозданием именно их.
                    state["person_late_minutes"][employee_id] += delta - grace
                    column["late"] += 1
                else:
                    counts["on_time"] += 1
                    state["on_time"] += 1
                    week["on_time"] += 1
                    column["early" if delta <= 0 else "grace"] += 1
                if delta > 0:
                    state["after_start"] += 1

                open_count = sum(1 for one in day_sessions if one.ended_at is None)
                state["open_sessions"] += open_count
                if open_count == 0:
                    seconds = sum(one.duration_seconds or 0 for one in day_sessions)
                    counts["closed_seconds"] += seconds
                    counts["closed_days"] += 1
                    state["closed_seconds"] += seconds
                    state["closed_days"] += 1
                    week["closed_seconds"] += seconds
                    week["closed_days"] += 1

        future_from = max(today.values())
        state["days"] = [
            _day_row(day, by_day.get(day, {}), weekday, future=day > future_from)
            for day in days_in(first, last)
        ]
        return state

    # ---------------------------------------------------------------- разрезы

    @staticmethod
    def _ranking(offices: list[Office], current: dict, previous: dict) -> list[dict]:
        rows = []
        for office in offices:
            now = _ratio(current["office_attended"][office.id], current["office_expected"][office.id])
            before = _ratio(previous["office_attended"][office.id], previous["office_expected"][office.id])
            rows.append({
                "id": str(office.id),
                "name": office.name,
                "attendance": now,
                "previous_attendance": before,
                "difference_points": _points(now, before),
            })
        # Больше явка — выше. Офис без рабочих дней в периоде — в конце:
        # «нет данных» не лучше и не хуже, его просто не с чем сравнить.
        rows.sort(key=lambda row: (row["attendance"]["percent"] is None, -(row["attendance"]["percent"] or 0), row["name"]))
        for position, row in enumerate(rows, start=1):
            row["position"] = position
        return rows

    @staticmethod
    def _regions(offices: list[Office], current: dict, previous: dict) -> list[dict]:
        """Явка по регионам — суммой их офисов.

        Регион без офисов в выборке не показывается вовсе: строка с
        прочерками не сообщает ничего, кроме того, что фильтр сузил
        выборку, — а это и так видно по фильтру.
        """
        now: dict = {}
        before: dict = {}
        names: dict = {}
        for office in offices:
            region = getattr(office, "region", None)
            key = str(region.id) if region else "—"
            names[key] = region.name if region else "Без региона"
            a, e = now.setdefault(key, [0, 0])
            a += current["office_attended"][office.id]
            e += current["office_expected"][office.id]
            now[key] = [a, e]
            pa, pe = before.setdefault(key, [0, 0])
            pa += previous["office_attended"][office.id]
            pe += previous["office_expected"][office.id]
            before[key] = [pa, pe]

        rows = []
        for key, (attended, expected) in now.items():
            was_attended, was_expected = before.get(key, [0, 0])
            was = _ratio(was_attended, was_expected)
            ratio = _ratio(attended, expected)
            rows.append({
                "id": key,
                "name": names[key],
                "attendance": ratio,
                "previous_attendance": was,
                "difference_points": _points(ratio, was),
            })
        rows.sort(key=lambda row: (
            row["attendance"]["percent"] is None,
            -(row["attendance"]["percent"] or 0),
            row["name"],
        ))
        for position, row in enumerate(rows, start=1):
            row["position"] = position
        return rows

    @staticmethod
    def _people(state: dict) -> list[dict]:
        """Сотрудники: явка и опоздания.

        Только те, кого в периоде хоть раз ждали: человек без рабочих
        дней не «худший по явке», его просто не с чем сравнить.

        Список ограничен: рейтинг на тысячу строк никто не читает, а
        весит он столько же, сколько вся остальная страница.
        """
        from humotech.employees.models import Employee

        ids = [one for one, count in state["person_expected"].items() if count]
        if not ids:
            return []

        names = dict(
            Employee.objects.filter(id__in=ids).values_list("id", "last_name")
        )
        firsts = dict(
            Employee.objects.filter(id__in=ids).values_list("id", "first_name")
        )

        rows = []
        for employee_id in ids:
            expected = state["person_expected"][employee_id]
            attended = state["person_attended"][employee_id]
            late = state["person_late"][employee_id]
            rows.append({
                "id": str(employee_id),
                "name": " ".join(
                    one for one in
                    [names.get(employee_id), firsts.get(employee_id)] if one
                ),
                "attendance": _ratio(attended, expected),
                "late_days": late,
                "late_minutes": state["person_late_minutes"][employee_id],
                "missed_days": expected - attended,
            })

        # Худшая явка сверху: страницу открывают, чтобы найти проблему,
        # а не полюбоваться отличниками.
        rows.sort(key=lambda row: (
            row["attendance"]["percent"] if row["attendance"]["percent"] is not None else 101,
            -row["late_days"],
            row["name"],
        ))
        return rows[:PEOPLE_LIMIT]

    @staticmethod
    def _arrivals(state: dict) -> dict:
        starts = state["starts"]
        common = starts.most_common(1)[0][0] if starts else None
        entries = state["entries"]
        median = round(statistics.median(entries)) if entries else None
        return {
            "bucket_minutes": BUCKET_MINUTES,
            "from_minutes": EARLIEST,
            "to_minutes": LATEST,
            "buckets": [
                {"from": EARLIEST + index * BUCKET_MINUTES, "to": EARLIEST + (index + 1) * BUCKET_MINUTES, **column}
                for index, column in enumerate(state["histogram"])
            ],
            #: Самое частое начало смены — подпись вертикальной линии.
            "start_time": common,
            #: Все ли в выборке начинают одинаково. Если нет, ось подписывается
            #: сдвигом от начала смены, а не часами.
            "uniform_start": len(starts) <= 1,
            "median_minutes": median,
            "after_start": state["after_start"],
            "late": _ratio(state["late"], state["arrivals"]),
        }

    @staticmethod
    def _weekdays(state: dict) -> dict:
        rows = []
        for weekday in range(1, 6):
            week = state["weekdays"].get(weekday, _week_counts())
            rows.append({
                "weekday": weekday,
                "attendance": _ratio(week["attended"], week["expected"]),
                "on_time": _ratio(week["on_time"], week["arrivals"]),
                "average_seconds": (
                    round(week["closed_seconds"] / week["closed_days"]) if week["closed_days"] else None
                ),
            })
        # Лучший день — по совокупности: доля явки и среднее время, каждое
        # отнесённое к лучшему значению недели. Один показатель в отрыве
        # от другого делал бы лучшим день, в который пришли и сразу ушли.
        known = [row for row in rows if row["attendance"]["percent"] is not None]
        best = None
        if known:
            top_attendance = max(row["attendance"]["percent"] for row in known) or 1
            top_seconds = max(row["average_seconds"] or 0 for row in known) or 1
            best = max(
                known,
                key=lambda row: row["attendance"]["percent"] / top_attendance
                + (row["average_seconds"] or 0) / top_seconds,
            )["weekday"]
        return {"days": rows, "best": best}


# --- вспомогательное -----------------------------------------------------------


#: Сколько человек показывает рейтинг. Больше никто не читает, а весит
#: такой список столько же, сколько вся остальная страница.
PEOPLE_LIMIT = 50


def _empty_state(offices: list[Office]) -> dict:
    ids = [office.id for office in offices]
    return {
        "expected": 0, "attended": 0, "missed": 0, "on_time": 0, "late": 0,
        "arrivals": 0, "after_start": 0, "vacation": 0, "sick": 0, "other": 0,
        "closed_seconds": 0, "closed_days": 0, "open_sessions": 0,
        "office_expected": dict.fromkeys(ids, 0),
        "office_attended": dict.fromkeys(ids, 0),
        # По людям — для рейтинга сотрудников. Словари, а не заранее
        # заполненные ключи: состав известен только после выборки.
        "person_expected": Counter(),
        "person_attended": Counter(),
        "person_late": Counter(),
        "person_late_minutes": Counter(),
        "person_office": {},
        "person_name": {},
        "weekdays": {},
        "starts": Counter(),
        "entries": [],
        "histogram": [
            {"early": 0, "grace": 0, "late": 0}
            for _ in range((LATEST - EARLIEST) // BUCKET_MINUTES)
        ],
        "days": [],
    }


def _day_counts() -> dict:
    return {
        "expected": 0, "attended": 0, "missed": 0, "on_time": 0, "late": 0,
        "vacation": 0, "sick": 0, "other": 0, "closed_seconds": 0, "closed_days": 0,
    }


def _week_counts() -> dict:
    return {"expected": 0, "attended": 0, "on_time": 0, "arrivals": 0, "closed_seconds": 0, "closed_days": 0}


def _day_row(day: date, counts: dict, weekday: int | None, *, future: bool) -> dict:
    counts = {**_day_counts(), **counts}
    arrivals = counts["on_time"] + counts["late"]
    return {
        "day": day.isoformat(),
        "weekday": day.isoweekday(),
        #: Рабочий ли день: по графику кого-то ждали. Выходной — `false`.
        "working": counts["expected"] > 0,
        #: День ещё не наступил хотя бы в одном офисе выборки.
        "future": future,
        #: Входит ли день в детализацию по дню недели.
        "in_detail": weekday is None or day.isoweekday() == weekday,
        "expected": counts["expected"],
        "attended": counts["attended"],
        "percent": _ratio(counts["attended"], counts["expected"])["percent"],
        "on_time": counts["on_time"],
        "on_time_percent": _ratio(counts["on_time"], arrivals)["percent"],
        "late": counts["late"],
        "missed": counts["missed"],
        "vacation": counts["vacation"],
        "sick_leave": counts["sick"],
        "other_absence": counts["other"],
        "average_seconds": (
            round(counts["closed_seconds"] / counts["closed_days"]) if counts["closed_days"] else None
        ),
    }


def _exceptions(actor: Actor, office_ids: list, first: date, last: date) -> dict:
    """Праздники и переносы по офисам: общие, поверх — свои у офиса."""
    result: dict = {office_id: {} for office_id in office_ids}
    rows = CalendarException.objects.filter(
        Q(office_id__in=office_ids) | Q(office__isnull=True),
        organization_id=actor.organization_id,
        date__gte=first,
        date__lte=last,
        is_active=True,
    )
    for row in sorted(rows, key=lambda one: one.office_id is not None):
        targets = office_ids if row.office_id is None else [row.office_id]
        for office_id in targets:
            result.setdefault(office_id, {})[row.date] = row.is_working_day
    return result


__all__ = ["BUCKET_MINUTES", "EARLIEST", "LATEST", "OverviewService"]
