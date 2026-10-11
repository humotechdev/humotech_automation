"""Что известно про рабочий день сотрудника: один ответ для всей системы.

До аудита 12.10 норму дня считали четыре формулы (недельные минуты на
число рабочих дней, длина смены, смена минус перерывы, фиксированные
8 часов), офис дня брали то текущий, то на дату, а правка графика
переписывала прошлое. Отсюда «Недостача 1 ч» у человека, выполнившего
норму, разные числа в боте и в CRM и сдвиг опозданий прошлых месяцев
(находки C-10, C-12, D-02, D-03).

Теперь любой расчёт — аналитика, табель, выгрузки, бот, Mini App,
отпуска, напоминания — спрашивает здесь:

* **офис на дату** — основное назначение, действующее в этот день, и его
  пояс (в день перевода — уже новый офис);
* **версия графика на дату** — назначение графика на день и версия этого
  графика, действовавшая в тот день (`WorkSchedule.version_of`);
* **календарь своего офиса** — исключение офиса сильнее общего
  исключения организации и действует только в этом офисе;
* **норма дня** — из самого графика: если у графика заведены перерывы,
  это смена минус неоплачиваемые перерывы; если перерывов нет — договорное
  недельное время (`weekly_minutes`), делённое на число рабочих дней. У
  графиков из CRM обе величины совпадают (форма считает недельное время
  как дни × (смена − перерыв)); у графиков без перерывов «09–18, 40 ч»
  смена целиком дала бы 9 часов и «недостачу» каждому, кто отработал 8.
  Это значение по умолчанию, а не решение владельца (вопрос П-3): если
  правило поменяется, меняется одна функция `_norm_seconds`;
* **конец дня** — момент, после которого рабочий день без отметки можно
  называть пропуском: конец смены по графику, а если времени смены нет —
  конец суток в поясе офиса (решение по D-01).

Чего здесь нет намеренно. Время смены и норма рабочего выходного по
переносу и сокращённого дня не выдумываются (вопрос П-11): у рабочего
выходного без времени в графике `norm_seconds is None`, и вызывающий сам
решает, как это показать. Ночные смены не поддерживаются (решение Р-9):
старые строки с концом раньше начала читаются с переносом конца на
следующие сутки, новые создать нельзя.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from django.db.models import Q

from humotech.core.timeframes import day_bounds, office_zone, zone

SHORT_DAY = "SHORT_DAY"
WORKING_WEEKEND = "WORKING_WEEKEND"


@dataclass(frozen=True)
class DayPlan:
    """План одного дня одного сотрудника."""

    employee_id: uuid.UUID
    day: date
    #: Офис основного назначения на этот день (None — назначения нет).
    office_id: uuid.UUID | None
    timezone: ZoneInfo
    #: Исходный график (тот, что назначен сотруднику) и его версия на день.
    schedule_id: uuid.UUID | None
    version_id: uuid.UUID | None
    #: None — графика на этот день нет: не «выходной» и не «рабочий».
    is_working: bool | None
    #: Время смены по графику. None у выходного и у рабочего дня по
    #: переносу, если в графике у этого дня недели времени нет.
    start: time | None
    end: time | None
    #: Норма в секундах. 0 у выходного. None — нормы нет: графика нет
    #: или правило для дня не определено (рабочий выходной без времени).
    norm_seconds: int | None
    late_grace_minutes: int = 0
    early_leave_grace_minutes: int = 0
    #: Тип исключения календаря, если оно действует в этот день.
    exception_type: str | None = None
    schedule_name: str | None = None

    @property
    def has_schedule(self) -> bool:
        return self.is_working is not None

    @property
    def starts_at(self) -> datetime | None:
        if self.start is None:
            return None
        return datetime.combine(self.day, self.start, tzinfo=self.timezone)

    @property
    def ends_at(self) -> datetime | None:
        if self.start is None or self.end is None:
            return None
        end = datetime.combine(self.day, self.end, tzinfo=self.timezone)
        if self.end <= self.start:
            # Старая ночная смена: конец — на следующие сутки.
            end += timedelta(days=1)
        return end

    @property
    def settled_at(self) -> datetime:
        """С какого момента день считается прошедшим.

        Рабочий день со временем смены — с конца смены. Иначе — с начала
        следующих суток в поясе офиса: раньше утверждать «не пришёл» нельзя.
        """
        if self.is_working and self.ends_at is not None:
            return self.ends_at
        return day_bounds(self.day, self.timezone)[1]

    def is_settled(self, now: datetime) -> bool:
        return now >= self.settled_at


# --------------------------------------------------------------------------
# норма дня

def shift_seconds(start: time, end: time) -> int:
    """Длина интервала в секундах; конец не позже начала — следующие сутки."""
    base = datetime.combine(date(2000, 1, 3), start)
    finish = datetime.combine(date(2000, 1, 3), end)
    if finish <= base:
        finish += timedelta(days=1)
    return int((finish - base).total_seconds())


def _norm_seconds(schedule_day, version=None) -> int:
    """Норма рабочего дня графика (значение по умолчанию, вопрос П-3).

    * Перерывы у графика заведены — смена минус неоплачиваемые перерывы
      (`is_paid=False`) этого дня.
    * Перерывов нет ни у одного дня — договорное недельное время, делённое
      на число рабочих дней версии: обед в графике просто не описан.
    """
    if schedule_day.start_time is None or schedule_day.end_time is None:
        return 0
    total = shift_seconds(schedule_day.start_time, schedule_day.end_time)
    source = version if version is not None else getattr(schedule_day, "schedule", None)
    days = list(source.days.all()) if source is not None else [schedule_day]
    has_breaks = any(day.breaks.all() for day in days if day.is_working_day)
    if not has_breaks and source is not None and source.weekly_minutes:
        working = sum(1 for day in days if day.is_working_day)
        if working:
            return int(source.weekly_minutes // working) * 60
    unpaid = sum(
        shift_seconds(item.start_time, item.end_time)
        for item in schedule_day.breaks.all()
        if not item.is_paid and item.end_time != item.start_time
    )
    return max(total - unpaid, 0)


def norm_of_day(schedule_day, version=None) -> int:
    """Норма дня графика (для форм, выгрузок и выездной работы)."""
    return _norm_seconds(schedule_day, version)


# --------------------------------------------------------------------------
# пакетная загрузка

def plans_for(
    employee_ids: Iterable[uuid.UUID], first: date, last: date
) -> dict[tuple[uuid.UUID, date], DayPlan]:
    """Планы всех дней периода для набора сотрудников — за пять запросов."""
    from humotech.employees.models import Employee, EmployeeAssignment
    from humotech.schedules.models import (
        CalendarException,
        EmployeeScheduleAssignment,
        WorkSchedule,
    )

    ids = list(dict.fromkeys(employee_ids))
    if not ids or first > last:
        return {}

    people = {
        row[0]: (row[1], row[2])
        for row in Employee.objects.filter(id__in=ids).values_list(
            "id", "organization_id", "organization__default_timezone")
    }

    places: dict[uuid.UUID, list] = defaultdict(list)
    offices: dict[uuid.UUID, object] = {}
    for row in (
        EmployeeAssignment.objects.filter(
            employee_id__in=ids, is_primary=True, valid_from__lte=last,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=first))
        .select_related("office", "office__organization")
        .order_by("valid_from")
    ):
        places[row.employee_id].append((row.office_id, row.valid_from, row.valid_to))
        offices[row.office_id] = row.office

    holdings: dict[uuid.UUID, list] = defaultdict(list)
    for row in (
        EmployeeScheduleAssignment.objects.filter(
            employee_id__in=ids, valid_from__lte=last,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=first))
        .order_by("valid_from")
    ):
        holdings[row.employee_id].append((row.schedule_id, row.valid_from, row.valid_to))

    roots = {schedule_id for rows in holdings.values() for schedule_id, _, _ in rows}
    families: dict[uuid.UUID, list] = defaultdict(list)
    names: dict[uuid.UUID, str] = {}
    if roots:
        for version in (
            WorkSchedule.objects.filter(Q(id__in=roots) | Q(version_of_id__in=roots))
            .prefetch_related("days__breaks")
        ):
            families[version.root_id].append(version)
            if version.version_of_id is None:
                names[version.id] = version.name

    organization_ids = {org for org, _ in people.values()}
    exceptions: dict[object, dict[date, tuple[bool, str]]] = defaultdict(dict)
    for row in CalendarException.objects.filter(
        Q(office_id__in=set(offices)) | Q(office__isnull=True),
        organization_id__in=organization_ids,
        date__gte=first, date__lte=last, is_active=True,
    ):
        key = row.office_id if row.office_id is not None else ("org", row.organization_id)
        exceptions[key][row.date] = (row.is_working_day, row.exception_type)

    days: list[date] = []
    one = first
    while one <= last:
        days.append(one)
        one += timedelta(days=1)

    result: dict[tuple[uuid.UUID, date], DayPlan] = {}
    for employee_id in ids:
        organization_id, org_tz = people.get(employee_id, (None, None))
        fallback = zone(org_tz)
        for one in days:
            office_id = _on(places.get(employee_id), one)
            office = offices.get(office_id)
            tz = office_zone(office) if office is not None else fallback
            root_id = _on(holdings.get(employee_id), one)
            override = None
            if office_id is not None:
                override = exceptions.get(office_id, {}).get(one)
            if override is None:
                override = exceptions.get(("org", organization_id), {}).get(one)
            version = version_on(families.get(root_id, ()), one) if root_id else None
            result[(employee_id, one)] = _plan(
                employee_id, one, office_id, tz, root_id, version, override,
                names.get(root_id),
            )
    return result


def plan_for(employee_id: uuid.UUID, day: date) -> DayPlan:
    return plans_for([employee_id], day, day)[(employee_id, day)]


def _on(periods, day: date):
    """Значение периода `(value, valid_from, valid_to)`, действующего в день."""
    for value, valid_from, valid_to in periods or ():
        if valid_from <= day and (valid_to is None or valid_to >= day):
            return value
    return None


def version_on(versions, day: date):
    """Версия графика, действующая в день (исходная строка — с открытым началом)."""
    for version in versions:
        if (version.valid_from is None or version.valid_from <= day) and (
            version.valid_to is None or version.valid_to >= day
        ):
            return version
    return None


def _plan(employee_id, day, office_id, tz, root_id, version, override, name) -> DayPlan:
    if root_id is None or version is None:
        return DayPlan(employee_id, day, office_id, tz, None, None, None, None, None, None)

    match = next((d for d in version.days.all() if d.weekday == day.isoweekday()), None)
    typical_working = bool(match and match.is_working_day)
    common = dict(
        employee_id=employee_id, day=day, office_id=office_id, timezone=tz,
        schedule_id=root_id, version_id=version.id,
        late_grace_minutes=version.late_grace_minutes or 0,
        early_leave_grace_minutes=version.early_leave_grace_minutes or 0,
        schedule_name=name,
    )

    if override is not None:
        working, kind = override
        if not working:
            return DayPlan(is_working=False, start=None, end=None, norm_seconds=0,
                           exception_type=kind, **common)
        if typical_working:
            # Рабочий и по графику, и по календарю. Сокращённый день пока
            # с полной нормой: правило не утверждено (П-11).
            return DayPlan(is_working=True, start=match.start_time, end=match.end_time,
                           norm_seconds=_norm_seconds(match, version), exception_type=kind, **common)
        # Рабочий выходной по переносу: день рабочий, а время смены и
        # норму график не задаёт — не выдумываем (П-11).
        return DayPlan(is_working=True, start=None, end=None, norm_seconds=None,
                       exception_type=kind, **common)

    if typical_working:
        return DayPlan(is_working=True, start=match.start_time, end=match.end_time,
                       norm_seconds=_norm_seconds(match, version), **common)
    return DayPlan(is_working=False, start=None, end=None, norm_seconds=0, **common)


def show_versions_on(schedules, day: date) -> None:
    """Подставить в объекты графиков дни и поля версии, действующей в `day`.

    Для экранов, которые показывают «текущий график» (список сотрудников,
    карточка, контекст обращения): назначения ссылаются на исходный
    график, а показывать надо расписание, действующее сегодня. Название и
    пояс остаются у исходного графика. Дни — в атрибуте `display_days`.
    """
    from humotech.schedules.models import WorkSchedule

    schedules = [one for one in schedules if one is not None]
    if not schedules:
        return
    roots = {one.id for one in schedules}
    family: dict = defaultdict(list)
    for version in (
        WorkSchedule.objects.filter(Q(id__in=roots) | Q(version_of_id__in=roots))
        .prefetch_related("days__breaks")
    ):
        family[version.root_id].append(version)
    for schedule in schedules:
        current = version_on(family.get(schedule.id, ()), day) or schedule
        schedule.display_days = list(current.days.all())
        for name in ("weekly_minutes", "late_grace_minutes",
                     "early_leave_grace_minutes", "is_flexible"):
            setattr(schedule, name, getattr(current, name))
