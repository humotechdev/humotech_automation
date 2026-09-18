"""Напоминание о начале дня и ответы «Опаздываю» / «Не приду».

**Одно сообщение за день и ни одним больше.** Человек, которому бот
написал трижды, отключает бота — и тогда он не получит ни напоминания,
ни уведомления о заявке. Поэтому повтор закрыт ключом идемпотентности
очереди: `day-start:<сотрудник>:<день>`. Эта строка — не оптимизация,
а свойство: сколько бы раз проверка ни прошла, сообщение одно.

**Кому не пишут вовсе.** Тому, кто и не должен был приходить: выходной
по графику, праздник, утверждённый отпуск или больничный. Написать
человеку в отпуске «вы сегодня будете в офисе?» — это не напоминание,
а повод перестать доверять боту. Уволенным и стажёрам-без-графика тоже
не пишут: первым незачем, про вторых сказать нечего.

**Окно, а не «после».** Проверка идёт каждые несколько минут, и без
верхней границы человек, забывший отметиться утром, получил бы вопрос
в восемь вечера. Поэтому напоминание живёт `WINDOW` часов после допуска
опоздания: не успели — значит день уже разбирают в CRM, а не в чате.

**«Не приду» ничего не оформляет.** Ни отпуска, ни больничного: они
проходят согласование и живут своими заявками. Сказанное в чате
объясняет пустую строку в табеле — и только. Иначе отсутствие
оформлялось бы одной кнопкой мимо HR.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from humotech.absences.models import EmployeeAbsence
from humotech.attendance.models import AttendanceEvent, DayNotice
from humotech.attendance.statistics import COUNTED_ABSENCE_STATUSES
from humotech.core.enums import DAY_NOTICE_KINDS
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.timeframes import day_bounds, office_zone
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.services import (
    WORKING_STATUSES,
    current_primary_assignment_filter,
)
from humotech.notifications import outbox
from humotech.telegram.models import TelegramAccount

logger = logging.getLogger("humotech.attendance.reminders")

#: Сколько ждать после начала смены, если график не сказал иначе.
DEFAULT_GRACE_MINUTES = 15

#: Сколько часов напоминание остаётся уместным.
WINDOW = timedelta(hours=4)

#: Статусы отсутствия, при которых человек и не должен приходить.
#: Тот же набор, что считает табель: разойдись они — человек в отпуске
#: получал бы вопрос «вы сегодня будете в офисе?».
SETTLED_ABSENCES = COUNTED_ABSENCE_STATUSES


@dataclass(frozen=True)
class Due:
    """Один человек, которому пора напомнить."""

    employee: Employee
    day: date
    #: Начало смены по графику, в поясе офиса.
    starts_at: datetime
    #: Во сколько смена начиналась — для текста сообщения.
    start_time: time


def message_for(start_time: time) -> str:
    """Текст напоминания.

    Вопрос, а не упрёк: человек мог застрять в пробке, заболеть или
    просто забыть приложить телефон. «Почему вы не отметились» ставит
    его оправдываться там, где достаточно спросить.
    """
    return (
        f"Ваш рабочий день начался в {start_time.strftime('%H:%M')}, "
        "но отметки ещё нет. Вы сегодня будете в офисе?"
    )


def due(now: datetime | None = None) -> list[Due]:
    """Кому пора напомнить прямо сейчас.

    Ничего не отправляет и ничего не пишет: чистый расчёт, который можно
    вызвать в тесте и посмотреть глазами.
    """
    moment = now or timezone.now()
    found: list[Due] = []

    assignments = (
        EmployeeAssignment.objects.filter(
            current_primary_assignment_filter(moment.date()),
            employee__employment_status__in=WORKING_STATUSES,
        )
        .select_related("employee", "office", "office__organization")
    )

    for assignment in assignments:
        office = assignment.office
        if office is None:
            continue
        zone = office_zone(office)
        local = moment.astimezone(zone)
        day = local.date()

        schedule = _shift_of(assignment.employee_id, day)
        if schedule is None:
            continue
        start_time, grace = schedule

        if not _is_working_day(office, day):
            continue

        starts_at = datetime.combine(day, start_time, tzinfo=zone)
        deadline = starts_at + timedelta(minutes=grace)
        if moment < deadline or moment > deadline + WINDOW:
            continue

        if _already_marked(assignment.employee_id, day, zone):
            continue
        if _is_away(assignment.employee_id, day, zone):
            continue
        if DayNotice.objects.filter(
            employee_id=assignment.employee_id, day=day
        ).exists():
            continue
        if not _reachable(assignment.employee_id):
            continue

        found.append(
            Due(
                employee=assignment.employee,
                day=day,
                starts_at=starts_at,
                start_time=start_time,
            )
        )
    return found


def run_once(now: datetime | None = None) -> int:
    """Поставить напоминания в очередь. Возвращает, сколько поставлено.

    Считаются именно новые. Очередь на повторный ключ возвращает прежнюю
    строку, а не отказ, — и считать её отправкой значило бы отчитываться
    о сообщениях, которых никто не получал.
    """
    items = {
        f"day-start:{one.employee.id}:{one.day}": one for one in due(now)
    }
    if not items:
        return 0

    from humotech.notifications.models import Notification

    already = set(
        Notification.objects.filter(idempotency_key__in=items)
        .values_list("idempotency_key", flat=True)
    )

    sent = 0
    for key, item in items.items():
        if key in already:
            continue
        outbox.enqueue(
            organization_id=item.employee.organization_id,
            employee_id=item.employee.id,
            notification_type="attendance.day_start",
            body=message_for(item.start_time),
            # Один день — одно напоминание, сколько бы раз проверка
            # ни прошла. Уникальный ключ в базе делает это свойством
            # схемы, а не аккуратности вызывающего.
            idempotency_key=key,
        )
        sent += 1
    if sent:
        logger.info("day start reminders queued", extra={"count": sent})
    return sent


def notice(
    *,
    employee: Employee,
    kind: str,
    comment: str | None = None,
    now: datetime | None = None,
) -> DayNotice:
    """Записать ответ человека: опаздывает или не придёт.

    Строка одна на человека и день: сказавший «опаздываю», а потом «не
    приду», не должен превращаться в две записи, из которых табель
    выберет случайную.
    """
    if kind not in DAY_NOTICE_KINDS:
        raise ValidationFailed(
            "Неизвестный вид сообщения",
            details={"kind": kind, "allowed": list(DAY_NOTICE_KINDS)},
        )

    moment = now or timezone.now()
    zone = _zone_of(employee)
    day = moment.astimezone(zone).date()

    text = (comment or "").strip() or None
    row, created = DayNotice.objects.update_or_create(
        employee_id=employee.id,
        day=day,
        defaults={
            "organization_id": employee.organization_id,
            "kind": kind,
            "comment": text,
            "noticed_at": moment,
        },
    )
    logger.info(
        "day notice recorded",
        extra={"employee_id": str(employee.id), "kind": kind, "new": created},
    )
    return row


def notices_of_day(
    employee_ids: list[uuid.UUID], day: date
) -> dict[uuid.UUID, DayNotice]:
    """Что сказали про этот день — одним запросом на всех."""
    return {
        row.employee_id: row
        for row in DayNotice.objects.filter(employee_id__in=employee_ids, day=day)
    }


# ------------------------------------------------------------------ внутреннее


def _shift_of(employee_id: uuid.UUID, day: date) -> tuple[time, int] | None:
    """Начало смены и допуск опоздания, если день рабочий по графику."""
    from humotech.schedules.models import EmployeeScheduleAssignment

    assignment = (
        EmployeeScheduleAssignment.objects.filter(
            employee_id=employee_id, valid_from__lte=day
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=day))
        .select_related("schedule")
        .prefetch_related("schedule__days")
        .order_by("-valid_from")
        .first()
    )
    if assignment is None:
        return None

    match = next(
        (
            one for one in assignment.schedule.days.all()
            if one.weekday == day.isoweekday()
        ),
        None,
    )
    if match is None or not match.is_working_day or match.start_time is None:
        return None

    # Ноль здесь — это «не задано», а не «напомнить ровно в девять
    # ноль-ноль»: допуск опоздания в графике по умолчанию нулевой, и
    # понимать его буквально значило бы писать человеку в ту же минуту,
    # когда он открывает дверь.
    grace = assignment.schedule.late_grace_minutes or DEFAULT_GRACE_MINUTES
    return match.start_time, grace


def _is_working_day(office, day: date) -> bool:
    """Не праздник ли. Исключение офиса перекрывает общее по организации."""
    from humotech.schedules.models import CalendarException

    rows = CalendarException.objects.filter(
        Q(office_id=office.id) | Q(office__isnull=True),
        organization_id=office.organization_id,
        date=day,
        is_active=True,
    )
    # Сортировка ставит общее правило раньше офисного: последнее слово
    # остаётся за офисом, у которого свой календарь.
    working = True
    for row in sorted(rows, key=lambda one: one.office_id is not None):
        working = row.is_working_day
    return working


def _already_marked(employee_id: uuid.UUID, day: date, zone) -> bool:
    """Отмечался ли человек сегодня. Любая принятая отметка считается."""
    start, end = day_bounds(day, zone)
    return AttendanceEvent.objects.filter(
        employee_id=employee_id,
        occurred_at__gte=start,
        occurred_at__lt=end,
        verification_status="ACCEPTED",
    ).exists()


def _is_away(employee_id: uuid.UUID, day: date, zone) -> bool:
    """Оформлено ли отсутствие, накрывающее этот день.

    Границы отсутствия хранятся моментами, и сравнивать их с датой
    напрямую нельзя: при переходе через полночь разница врёт на сутки.
    """
    start, end = day_bounds(day, zone)
    return EmployeeAbsence.objects.filter(
        employee_id=employee_id,
        status__in=SETTLED_ABSENCES,
        start_at__lt=end,
        end_at__gte=start,
    ).exists()


def _reachable(employee_id: uuid.UUID) -> bool:
    """Есть ли куда писать. Без привязки сообщение копилось бы впустую."""
    return TelegramAccount.objects.filter(
        employee_id=employee_id, status="ACTIVE"
    ).exists()


def _zone_of(employee: Employee) -> ZoneInfo:
    place = (
        EmployeeAssignment.objects.filter(employee_id=employee.id, is_primary=True)
        .select_related("office", "office__organization")
        .order_by("-valid_from")
        .first()
    )
    return office_zone(place.office if place else None)


__all__ = [
    "DEFAULT_GRACE_MINUTES",
    "Due",
    "WINDOW",
    "due",
    "message_for",
    "notice",
    "notices_of_day",
    "run_once",
]
