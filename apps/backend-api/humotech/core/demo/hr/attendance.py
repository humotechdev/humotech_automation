"""Отметки за месяц и картина сегодняшнего дня.

Отметки строятся по графику КОНКРЕТНОГО человека. В дни подтверждённого
отсутствия отметок нет. Сегодняшний день собирается по ролям сценария:
кто опоздал, кто не отметился, у кого отметка вне геозоны.

Из этих событий и смен дашборд, посещаемость и аналитика считают всё
сами; готовых чисел здесь нет.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from humotech.attendance.models import AttendanceEvent, AttendanceSession, DayNotice
from humotech.core.demo.hr import catalog as cat
from humotech.core.demo.hr.people import Person, Structure, did, rng

HISTORY_DAYS = 30

LATE_NOTES = ("Пробки на кольцевой, буду к 09:30.", "Отвожу ребёнка в садик, задержусь на 20 минут.",
              "Задержался в банке по рабочему вопросу.")


def build(org, structure: Structure, people: list[Person], today: date, now: datetime, tz,
          absent: set[tuple]) -> dict:
    events: list[AttendanceEvent] = []
    sessions: list[AttendanceSession] = []
    notices: list[DayNotice] = []
    schedules = {one.code: one for one in cat.SCHEDULES}

    def session(person, day, entry, exit_at, *, part=0, inside=True):
        office = structure.offices[person.office]
        points = structure.points[person.office]
        r = rng("point", person.number, day, part)
        point = points[r.randrange(len(points))]
        entry_event = AttendanceEvent(
            id=did("event", person.number, day, part, "in"), organization=org, employee=person.employee,
            office=office, qr_point=point, event_type="ENTRY", source="QR", verification_status="ACCEPTED",
            occurred_at=entry, inside_geofence=inside, inside_office_network=None,
            distance_m=None if inside else 420,
        )
        events.append(entry_event)
        exit_event = None
        if exit_at is not None:
            exit_event = AttendanceEvent(
                id=did("event", person.number, day, part, "out"), organization=org, employee=person.employee,
                office=office, qr_point=point, event_type="EXIT", source="QR", verification_status="ACCEPTED",
                occurred_at=exit_at, inside_geofence=True, inside_office_network=None,
            )
            events.append(exit_event)
        sessions.append(AttendanceSession(
            id=did("session", person.number, day, part), organization=org, employee=person.employee,
            office=office, entry_event=entry_event, exit_event=exit_event, started_at=entry, ended_at=exit_at,
            duration_seconds=int((exit_at - entry).total_seconds()) if exit_at else None,
            status="CLOSED" if exit_at else "OPEN",
        ))

    stale_days = {}
    for at, person in enumerate(p for p in people if "stale" in p.roles):
        stale_days[person.number] = today - timedelta(days=3 + 2 * at)

    for person in people:
        spec = schedules[person.schedule]
        for back in range(HISTORY_DAYS, -1, -1):
            day = today - timedelta(days=back)
            if day.isoweekday() not in spec.weekdays:
                continue
            if day < person.hire or (person.termination and day >= person.termination):
                continue
            if (person.employee.id, day) in absent:
                continue
            r = rng("day", person.number, day)
            start = datetime.combine(day, spec.start, tzinfo=tz)
            end = datetime.combine(day, spec.end, tzinfo=tz)

            if day == today:
                _today(person, spec, start, end, now, r, session, notices, org, day, tz)
                continue

            if r.random() < 0.015:
                continue  # отметки нет вовсе
            late = r.random() < 0.04
            arrival = r.randint(11, 30) if late else r.randint(-15, 3)
            entry = start + timedelta(minutes=arrival, seconds=r.randint(0, 59))
            leave = r.randint(-5, 25) - (r.randint(5, 20) if day.isoweekday() == 5 else 0)
            exit_at = end + timedelta(minutes=leave, seconds=r.randint(0, 59))
            if stale_days.get(person.number) == day:
                session(person, day, entry, None)  # ушёл, не отметив выход
                continue
            if r.random() < 0.12 and spec.lunch:
                out = datetime.combine(day, spec.lunch[0], tzinfo=tz) + timedelta(minutes=r.randint(0, 15))
                back_in = out + timedelta(minutes=r.randint(35, 55))
                session(person, day, entry, out, part=0)
                session(person, day, back_in, exit_at, part=1)
            else:
                session(person, day, entry, exit_at)
            if late and r.random() < 0.3:
                notices.append(DayNotice(
                    id=did("notice", person.number, day), organization=org, employee=person.employee, day=day,
                    kind="LATE", comment=LATE_NOTES[r.randrange(len(LATE_NOTES))],
                    noticed_at=start - timedelta(minutes=25),
                ))

    AttendanceEvent.objects.bulk_create(events, batch_size=1000)
    AttendanceSession.objects.bulk_create(sessions, batch_size=1000)
    DayNotice.objects.bulk_create(notices, batch_size=500)
    return {"events": len(events), "sessions": len(sessions), "notices": len(notices)}


def _today(person, spec, start, end, now, r, session, notices, org, day, tz) -> None:
    roles = person.roles
    if roles & {"no_mark", "waiting_cert", "hr_review"}:
        return
    if roles & {"late", "late_notice"}:
        arrival = r.randint(12, 35) if "late" in roles else r.randint(15, 30)
        if "late_notice" in roles:
            notices.append(DayNotice(
                id=did("notice", person.number, day), organization=org, employee=person.employee, day=day,
                kind="LATE", comment=LATE_NOTES[person.order % len(LATE_NOTES)],
                noticed_at=start - timedelta(minutes=30),
            ))
    else:
        # Вовремя — в пределах самого короткого допуска графиков (5 минут).
        arrival = r.randint(-15, 3)
    entry = start + timedelta(minutes=arrival, seconds=r.randint(0, 59))
    if entry > now:
        return  # ещё не пришёл: смена впереди
    if "stale" in roles:
        # Незакрытая смена у него — в прошлом; сегодня был утром и уехал.
        leave = entry + timedelta(hours=3, minutes=r.randint(10, 40))
        if leave < now:
            session(person, day, entry, leave)
        return
    exit_at = end + timedelta(minutes=r.randint(-5, 20))
    session(person, day, entry, exit_at if exit_at < now else None, inside="geofence" not in roles)
