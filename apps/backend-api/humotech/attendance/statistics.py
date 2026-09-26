"""Сколько человек пробыл в офисе: за день, за неделю, за месяц, за период.

Один модуль на все три экрана и на бота. Бот не считает ничего сам — иначе
«за неделю» в чате и «за неделю» в приложении разошлись бы, и выяснить,
какая цифра верна, было бы нечем.

Решения, которые здесь приняты раз и навсегда:

**Границы суток — в поясе офиса** (`humotech/core/timeframes.py`). Не сервера
и не графика: сотрудник видит статистику там, где работает, и «сегодня»
на экране обязано совпадать с «сегодня» за окном.

**Сессия принадлежит дню своего НАЧАЛА.** Ночная смена с 22:00 третьего
до 06:00 четвёртого целиком относится к третьему: для человека это «смена
в ночь на четвёртое», одна смена, а не две половинки. Плата за это видна
и названа честно: смена, начавшаяся в 22:00 в воскресенье, не попадает
в неделю, которая началась в понедельник, — вместе с теми шестью часами,
которые в неё фактически пришлись. Обратный вариант — резать сессию
пополам в полночь — ломает «сколько длилась эта сессия» в истории, а её
спрашивают чаще.

Из этого правила следует полезное свойство: итог периода РАВЕН сумме
дневных итогов по построению, а не потому, что мы за этим следим.
Сессия попадает в период тогда и только тогда, когда её начало попадает
в `[начало, конец)`.

**Незакрытая сессия не получает выдуманного выхода.** Её время считается
«по состоянию на сейчас» и помечается как предварительное. Дорисовать
человеку конец рабочего дня, которого не было, значит соврать в документе,
по которому считают зарплату.

**Часы в офисе — это часы в офисе.** Перерывы не вычитаются, хотя
`ScheduleBreak.is_paid` в схеме есть: здесь считается присутствие, а не
оплачиваемое время. Это разные числа, и расчётный отдел получит своё
из тех же событий, но по своим правилам.

**Нет графика — нет и рабочих дней.** Не ноль, а «неизвестно»: ноль
рабочих дней при нуле пропусков читается как безупречная посещаемость,
а это совсем другое утверждение.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from humotech.absences.models import EmployeeAbsence
from humotech.attendance.models import AttendanceSession
from humotech.core.timeframes import (
    days_in,
    local_date,
    month_range,
    range_bounds,
    today as today_in,
    week_range,
)
from humotech.schedules.models import CalendarException, EmployeeScheduleAssignment

# Как вид отсутствия ложится в колонки статистики.
#
# Коды заводит организация, и список ниже — только те, что создаёт `seed`.
# Всё незнакомое идёт в «прочее» намеренно: организация, добавившая свой
# вид отсутствия, не должна молча оказаться в колонке отпусков.
SICK_CODES = frozenset({"SICK_LEAVE"})
VACATION_CODES = frozenset({"ANNUAL_LEAVE", "UNPAID_LEAVE"})

# Отменённое отсутствие — это отсутствие, которого не было.
COUNTED_ABSENCE_STATUSES = ("PLANNED", "ACTIVE", "COMPLETED")


@dataclass(frozen=True)
class SessionRecord:
    """Одна сессия: пришёл — ушёл."""

    id: str
    day: date
    started_at: datetime
    ended_at: datetime | None
    seconds: int
    is_open: bool
    office_name: str | None
    entry_point_name: str | None
    exit_point_name: str | None

    @property
    def is_preliminary(self) -> bool:
        """Время открытой сессии посчитано «на сейчас» и ещё вырастет."""
        return self.is_open


@dataclass(frozen=True)
class DayRecord:
    day: date
    sessions: tuple[SessionRecord, ...]
    seconds: int
    has_open_session: bool
    # None = графика на этот день нет. Не «выходной» и не «рабочий».
    is_working_day: bool | None
    absence_code: str | None
    absence_name: str | None
    # Норма дня в секундах. None — графика на этот день нет, 0 — выходной.
    #
    # Не «конец смены минус начало»: в эту разницу входит обед, которого
    # в графике нет ни минутой. Норма берётся из `weekly_minutes` —
    # договорного недельного времени — и делится на число рабочих дней
    # графика, ровно как это уже делает `_daily_norm_minutes`
    # в ассистенте. Одно правило на весь проект, а не два похожих.
    norm_seconds: int | None

    @property
    def attended(self) -> bool:
        return bool(self.sessions)

    @property
    def missed(self) -> bool:
        """Рабочий день, в который не пришли и отсутствие не оформлено."""
        return (
            self.is_working_day is True
            and not self.attended
            and self.absence_code is None
        )


@dataclass(frozen=True)
class PeriodReport:
    first: date
    last: date
    timezone: str
    days: tuple[DayRecord, ...]
    has_schedule: bool

    @property
    def seconds(self) -> int:
        return sum(day.seconds for day in self.days)

    @property
    def completed_sessions(self) -> int:
        return sum(
            1 for day in self.days for s in day.sessions if not s.is_open
        )

    @property
    def open_sessions(self) -> int:
        return sum(1 for day in self.days for s in day.sessions if s.is_open)

    @property
    def attended_days(self) -> int:
        return sum(1 for day in self.days if day.attended)

    @property
    def working_days(self) -> int | None:
        if not self.has_schedule:
            return None
        return sum(1 for day in self.days if day.is_working_day)

    @property
    def missed_days(self) -> int | None:
        if not self.has_schedule:
            return None
        return sum(1 for day in self.days if day.missed)

    @property
    def sick_leave_days(self) -> int:
        return sum(1 for day in self.days if day.absence_code in SICK_CODES)

    @property
    def vacation_days(self) -> int:
        return sum(1 for day in self.days if day.absence_code in VACATION_CODES)

    @property
    def other_absence_days(self) -> int:
        return sum(
            1
            for day in self.days
            if day.absence_code is not None
            and day.absence_code not in SICK_CODES
            and day.absence_code not in VACATION_CODES
        )


# --- готовые периоды -------------------------------------------------------

def for_today(context, *, now: datetime | None = None) -> PeriodReport:
    day = today_in(context.timezone, now=now)
    return for_period(context, day, day, now=now)


def for_week(context, *, now: datetime | None = None) -> PeriodReport:
    first, last = week_range(today_in(context.timezone, now=now))
    return for_period(context, first, last, now=now)


def for_month(context, *, now: datetime | None = None) -> PeriodReport:
    first, last = month_range(today_in(context.timezone, now=now))
    return for_period(context, first, last, now=now)


def for_period(
    context, first: date, last: date, *, now: datetime | None = None
) -> PeriodReport:
    """Отчёт за произвольный период, заданный датами в поясе офиса."""
    moment = now or timezone.now()
    tz = context.timezone
    employee_id = context.employee.id

    sessions_by_day = _sessions_by_day(employee_id, first, last, tz, moment)
    plans = _day_plans(context, first, last)
    absences = _absence_days(context, first, last, tz)

    days = tuple(
        DayRecord(
            day=day,
            sessions=tuple(sessions_by_day.get(day, ())),
            seconds=sum(s.seconds for s in sessions_by_day.get(day, ())),
            has_open_session=any(s.is_open for s in sessions_by_day.get(day, ())),
            is_working_day=plans[day].is_working if day in plans else None,
            absence_code=absences.get(day, (None, None))[0],
            absence_name=absences.get(day, (None, None))[1],
            norm_seconds=plans[day].norm_seconds if day in plans else None,
        )
        for day in days_in(first, last)
    )
    return PeriodReport(
        first=first,
        last=last,
        timezone=str(tz),
        days=days,
        has_schedule=any(day.is_working_day is not None for day in days),
    )


# --- текущее состояние -----------------------------------------------------

class Presence:
    IN_OFFICE = "IN_OFFICE"
    OUTSIDE = "OUTSIDE"
    SICK_LEAVE = "SICK_LEAVE"
    VACATION = "VACATION"
    OTHER_ABSENCE = "OTHER_ABSENCE"
    DAY_OFF = "DAY_OFF"
    WORKDAY_MISSED = "WORKDAY_MISSED"


@dataclass(frozen=True)
class CurrentStatus:
    state: str
    day: date
    timezone: str
    open_session: SessionRecord | None
    last_entry_at: datetime | None
    last_exit_at: datetime | None
    seconds_today: int
    scheduled_start: time | None
    scheduled_end: time | None
    absence_name: str | None

    @property
    def is_inside(self) -> bool:
        return self.state == Presence.IN_OFFICE


def current_status(context, *, now: datetime | None = None) -> CurrentStatus:
    """Что показать на главном экране прямо сейчас.

    Открытая сессия и «часы сегодня» — РАЗНЫЕ числа, и обе отдаются
    отдельно. В три часа ночи у человека, зашедшего в 22:00, «сегодня»
    честно ноль, а в офисе он пять часов: сессия принадлежит вчерашнему
    дню. Свести их в одно число значило бы соврать в одном из двух мест.
    """
    moment = now or timezone.now()
    tz = context.timezone
    day = today_in(tz, now=moment)
    report = for_period(context, day, day, now=moment)
    record = report.days[0]

    open_session = _open_session(context, moment)
    absences = _absence_days(context, day, day, tz)
    absence_code, absence_name = absences.get(day, (None, None))

    scheduled_start, scheduled_end = _scheduled_times(context, day)
    last_entry, last_exit = _last_marks(context.employee.id)

    return CurrentStatus(
        state=_presence(
            open_session=open_session,
            absence_code=absence_code,
            is_working_day=record.is_working_day,
            attended=record.attended,
            scheduled_end=scheduled_end,
            day=day,
            tz=tz,
            now=moment,
        ),
        day=day,
        timezone=str(tz),
        open_session=open_session,
        last_entry_at=last_entry,
        last_exit_at=last_exit,
        seconds_today=record.seconds,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        absence_name=absence_name,
    )


def _presence(
    *, open_session, absence_code, is_working_day, attended, scheduled_end,
    day, tz, now,
) -> str:
    # Порядок разбора — это и есть правило. Человек в офисе считается
    # находящимся в офисе, даже если на этот день оформлен отпуск: факт
    # важнее плана, и расхождение должно быть видно, а не спрятано.
    if open_session is not None:
        return Presence.IN_OFFICE
    if absence_code in SICK_CODES:
        return Presence.SICK_LEAVE
    if absence_code in VACATION_CODES:
        return Presence.VACATION
    if absence_code is not None:
        return Presence.OTHER_ABSENCE
    if is_working_day is False:
        return Presence.DAY_OFF
    if is_working_day and not attended:
        # «Не пришёл» — только когда день уже кончился. В десять утра
        # человек ещё может быть в дороге, и объявлять прогул рано.
        if scheduled_end is None:
            return Presence.OUTSIDE
        finished = datetime.combine(day, scheduled_end, tzinfo=tz)
        return (
            Presence.WORKDAY_MISSED if now > finished.astimezone(tz)
            else Presence.OUTSIDE
        )
    return Presence.OUTSIDE


# --- сборка данных ---------------------------------------------------------

def _sessions_by_day(
    employee_id, first: date, last: date, tz: ZoneInfo, now: datetime
) -> dict[date, list[SessionRecord]]:
    start, end = range_bounds(first, last, tz)
    rows = (
        AttendanceSession.objects.filter(
            employee_id=employee_id, started_at__gte=start, started_at__lt=end
        )
        .exclude(status="INVALID")
        .select_related(
            "office", "entry_event", "entry_event__qr_point",
            "exit_event", "exit_event__qr_point",
        )
        .order_by("started_at")
    )

    grouped: dict[date, list[SessionRecord]] = {}
    for row in rows:
        is_open = row.status == "OPEN"
        if is_open:
            # Никакого выдуманного конца: считаем «на сейчас».
            seconds = max(int((now - row.started_at).total_seconds()), 0)
        else:
            seconds = row.duration_seconds or 0
        day = local_date(row.started_at, tz)
        grouped.setdefault(day, []).append(
            SessionRecord(
                id=str(row.id),
                day=day,
                started_at=row.started_at,
                ended_at=row.ended_at,
                seconds=seconds,
                is_open=is_open,
                office_name=row.office.name if row.office_id else None,
                entry_point_name=_point_name(row.entry_event),
                exit_point_name=_point_name(row.exit_event),
            )
        )
    return grouped


def _point_name(event) -> str | None:
    if event is None or event.qr_point_id is None:
        return None
    return event.qr_point.name


@dataclass(frozen=True)
class DayPlan:
    """Что график говорит про один день: рабочий ли он и какова норма."""

    is_working: bool
    # 0 у выходного: график про этот день знает и отвечает «нисколько».
    # Отсутствие дня в словаре — другое: графика нет вовсе.
    norm_seconds: int


def _day_plans(context, first: date, last: date) -> dict[date, DayPlan]:
    """Какие дни периода рабочие и сколько в каждом нормы.

    График читается на каждый день отдельно, а не один раз «текущий»:
    в месяце, внутри которого график поменяли, одна выборка дала бы
    неверное число рабочих дней ровно на половину периода.
    """
    assignments = list(
        EmployeeScheduleAssignment.objects.filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=first),
            employee_id=context.employee.id,
            valid_from__lte=last,
        )
        .select_related("schedule")
        .prefetch_related("schedule__days")
        .order_by("valid_from")
    )
    if not assignments:
        return {}

    exceptions = _calendar_exceptions(context, first, last)

    result: dict[date, DayPlan] = {}
    for day in days_in(first, last):
        schedule = _schedule_on(assignments, day)
        if schedule is None:
            continue
        norm = _daily_norm_seconds(schedule)
        override = exceptions.get(day)
        if override is not None:
            # Перенос делает день рабочим или выходным, но своей нормы
            # не приносит: в календарном исключении её просто нет. Значит,
            # у рабочего дня по переносу норма та же, что у остальных.
            result[day] = DayPlan(override, norm if override else 0)
            continue
        # `ScheduleDay.weekday` — ISO-8601: 1 = понедельник.
        # `date.weekday()` считает с нуля, отсюда `isoweekday()`.
        match = next(
            (d for d in schedule.days.all() if d.weekday == day.isoweekday()), None
        )
        working = bool(match and match.is_working_day)
        result[day] = DayPlan(working, norm if working else 0)
    return result


def _daily_norm_seconds(schedule) -> int:
    """Норма одного рабочего дня графика.

    Недельное договорное время, делённое на число рабочих дней недели.
    Ни «восемь часов по умолчанию», ни разница между концом и началом
    смены: в первом случае число взято с потолка, во втором в него
    попадает обед, которого график не описывает.

    Ноль, если рабочих дней в графике нет вовсе, — делить не на что,
    а выдумывать знаменатель здесь нечем.
    """
    working = sum(1 for day in schedule.days.all() if day.is_working_day)
    if not working or not schedule.weekly_minutes:
        return 0
    return int(schedule.weekly_minutes // working) * 60


def _schedule_on(assignments, day: date):
    for assignment in assignments:
        if assignment.valid_from <= day and (
            assignment.valid_to is None or assignment.valid_to >= day
        ):
            return assignment.schedule
    return None


def _calendar_exceptions(context, first: date, last: date) -> dict[date, bool]:
    """Праздники и переносы. Исключение офиса перекрывает общее.

    Одна дата может иметь и строку офиса, и строку на всю организацию —
    уникальные ключи это разрешают. Побеждает офис: он конкретнее.
    """
    rows = CalendarException.objects.filter(
        Q(office_id=context.office.id) | Q(office__isnull=True),
        organization_id=context.organization_id,
        date__gte=first,
        date__lte=last,
        is_active=True,
    )
    # Сначала общие по организации, затем офисные поверх них: сортировка
    # по `office_id` с NULL впереди делает перезапись правильной без
    # разбора случаев.
    result: dict[date, bool] = {}
    for row in sorted(rows, key=lambda r: r.office_id is not None):
        result[row.date] = row.is_working_day
    return result


def _absence_days(
    context, first: date, last: date, tz: ZoneInfo
) -> dict[date, tuple[str, str]]:
    """Дни периода, закрытые отсутствием, — по локальным датам.

    Границы отсутствия хранятся моментами времени, и вычитать их друг из
    друга нельзя: разница в днях врёт на сутки при любом переходе через
    полночь. Считаем перекрытие по дням в поясе офиса.
    """
    start, end = range_bounds(first, last, tz)
    rows = (
        EmployeeAbsence.objects.filter(
            employee_id=context.employee.id,
            status__in=COUNTED_ABSENCE_STATUSES,
            start_at__lt=end,
            end_at__gte=start,
        )
        .select_related("absence_type")
        .order_by("start_at")
    )

    result: dict[date, tuple[str, str]] = {}
    for row in rows:
        from_day = max(local_date(row.start_at, tz), first)
        to_day = min(local_date(row.end_at, tz), last)
        if to_day < from_day:
            continue
        for day in days_in(from_day, to_day):
            # Первое по времени отсутствие выигрывает: два наложившихся —
            # это ошибка данных, и показывать надо одно, а не оба.
            result.setdefault(day, (row.absence_type.code, row.absence_type.name))
    return result


def _open_session(context, now: datetime) -> SessionRecord | None:
    row = (
        AttendanceSession.objects.filter(
            employee_id=context.employee.id, status="OPEN"
        )
        .select_related("office", "entry_event", "entry_event__qr_point")
        .first()
    )
    if row is None:
        return None
    return SessionRecord(
        id=str(row.id),
        day=local_date(row.started_at, context.timezone),
        started_at=row.started_at,
        ended_at=None,
        seconds=max(int((now - row.started_at).total_seconds()), 0),
        is_open=True,
        office_name=row.office.name if row.office_id else None,
        entry_point_name=_point_name(row.entry_event),
        exit_point_name=None,
    )


def _scheduled_times(context, day: date) -> tuple[time | None, time | None]:
    assignment = (
        EmployeeScheduleAssignment.objects.filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=day),
            employee_id=context.employee.id,
            valid_from__lte=day,
        )
        .select_related("schedule")
        .prefetch_related("schedule__days")
        .first()
    )
    if assignment is None:
        return None, None
    match = next(
        (
            d
            for d in assignment.schedule.days.all()
            if d.weekday == day.isoweekday()
        ),
        None,
    )
    if match is None or not match.is_working_day:
        return None, None
    return match.start_time, match.end_time


def _last_marks(employee_id) -> tuple[datetime | None, datetime | None]:
    from humotech.attendance.models import AttendanceEvent

    def latest(event_type: str):
        row = (
            AttendanceEvent.objects.filter(
                employee_id=employee_id,
                event_type=event_type,
                verification_status="ACCEPTED",
            )
            .order_by("-occurred_at")
            .values_list("occurred_at", flat=True)
            .first()
        )
        return row

    return latest("ENTRY"), latest("EXIT")


__all__ = [
    "CurrentStatus",
    "DayRecord",
    "PeriodReport",
    "Presence",
    "SessionRecord",
    "current_status",
    "for_month",
    "for_period",
    "for_today",
    "for_week",
]
