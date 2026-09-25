"""Структура и люди: офисы, отделы, должности, графики, двести сотрудников.

Каждая строка получает ключ `uuid5` от читаемого имени (`employee:HT-0142`):
повторный запуск даёт те же ключи, а очистка удаляет ровно их.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from humotech.audit.models import AuditLog
from humotech.core.demo.hr import catalog as cat
from humotech.departments.models import Department
from humotech.employees.lifecycle import PROBATION_FAILED
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications.isolation import DEMO_CHAT_ID_FLOOR
from humotech.offices.models import Office
from humotech.positions.models import Position
from humotech.qr_codes.models import OfficeQrPoint
from humotech.regions.models import Region
from humotech.schedules.models import EmployeeScheduleAssignment, ScheduleBreak, ScheduleDay, WorkSchedule
from humotech.telegram.models import TelegramAccount

NAMESPACE = uuid.UUID("4b1c7a52-9d0e-4f3a-8c6b-2a7d5e9f1c30")


def did(*parts) -> uuid.UUID:
    """Постоянный ключ строки витрины по её читаемому имени."""
    return uuid.uuid5(NAMESPACE, "|".join(str(one) for one in parts))


def rng(*parts) -> random.Random:
    """Случайность от имени, а не от часов: повтор даёт то же самое."""
    return random.Random(str(did("rng", *parts)))


@dataclass
class Person:
    order: int
    number: str
    employee: Employee
    office: str
    department: str
    schedule: str
    status: str  # ACTIVE / PROBATION / TERMINATED
    telegram: str  # ACTIVE / REVOKED / NONE
    head: bool = False
    hire: date | None = None
    termination: date | None = None
    #: Особая роль в сценарии: vacation, sick, trip, no_mark, late, late_notice,
    #: geofence, stale, waiting_cert, hr_review, needs_fix, trainee_*, promoted, failed.
    roles: set[str] = field(default_factory=set)
    #: Прежний отдел, если человека перевели в последний месяц.
    moved_from: str | None = None
    moved_on: date | None = None

    @property
    def active(self) -> bool:
        return self.status in ("ACTIVE", "PROBATION")

    @property
    def name(self) -> str:
        return f"{self.employee.last_name} {self.employee.first_name}"


class Structure:
    """Справочники витрины, созданные в базе."""

    def __init__(self) -> None:
        self.offices: dict[str, Office] = {}
        self.points: dict[str, list[OfficeQrPoint]] = {}
        self.departments: dict[str, Department] = {}
        self.positions: dict[tuple[str, str], Position] = {}
        self.schedules: dict[str, WorkSchedule] = {}


def build_structure(org) -> Structure:
    result = Structure()
    for item in cat.OFFICES:
        region = Region.objects.get(organization=org, code=item.region_code)
        office = Office.objects.create(
            id=did("office", item.code), organization=org, region=region, code=f"{cat.MARK}-{item.code}",
            name=item.name, address=item.address, timezone=org.default_timezone, status="ACTIVE",
            latitude=item.latitude, longitude=item.longitude, geofence_radius_m=150,
        )
        result.offices[item.code] = office
        points = []
        for suffix, title, direction in (("IN", "Главный вход", "BOTH"), ("SIDE", "Служебный вход", "BOTH")):
            if suffix == "SIDE" and item.staff < 50:
                continue
            points.append(OfficeQrPoint.objects.create(
                id=did("qr", item.code, suffix), organization=org, office=office,
                code=f"{cat.MARK}-{item.code}-{suffix}", name=title, direction_mode=direction,
                qr_mode="ROTATING", rotation_seconds=30,
            ))
        result.points[item.code] = points
    for item in cat.DEPARTMENTS:
        result.departments[item.code] = Department.objects.create(
            id=did("department", item.code), organization=org, office=result.offices["HQ"],
            code=f"{cat.MARK}-{item.code}", name=item.name, status="ACTIVE",
        )
        for title in (item.head, *item.positions, item.trainee):
            result.positions[(item.code, title)] = Position.objects.create(
                id=did("position", item.code, title), organization=org,
                code=f"{cat.MARK}-{item.code}-{len(result.positions) + 1}", name=title, status="ACTIVE",
            )
    for item in cat.SCHEDULES:
        schedule = WorkSchedule.objects.create(
            id=did("schedule", item.code), organization=org, name=item.name, timezone=org.default_timezone,
            weekly_minutes=len(item.weekdays) * _minutes(item.start, item.end, item.lunch),
            late_grace_minutes=item.grace, early_leave_grace_minutes=10, is_flexible=False, status="ACTIVE",
        )
        for weekday in range(1, 8):
            working = weekday in item.weekdays
            day = ScheduleDay.objects.create(
                id=did("schedule-day", item.code, weekday), schedule=schedule, weekday=weekday,
                is_working_day=working, start_time=item.start if working else None,
                end_time=item.end if working else None,
            )
            if working and item.lunch:
                ScheduleBreak.objects.create(
                    id=did("schedule-break", item.code, weekday), schedule_day=day, name="Обед",
                    start_time=item.lunch[0], end_time=item.lunch[1], is_paid=False,
                )
        result.schedules[item.code] = schedule
    return result


def _minutes(start: time, end: time, lunch) -> int:
    total = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    if lunch:
        total -= (lunch[1].hour * 60 + lunch[1].minute) - (lunch[0].hour * 60 + lunch[0].minute)
    return total


# --- люди ---------------------------------------------------------------------------------


def plan_people() -> list[dict]:
    """Кто есть кто: отдел, офис, статус, Telegram и роль в сценарии дня.

    Порядок фиксирован, поэтому повторный запуск раздаёт те же роли тем
    же табельным номерам.
    """
    departments = [item.code for item in cat.DEPARTMENTS for _ in range(item.staff)]
    offices = [item.code for item in cat.OFFICES for _ in range(item.staff)]
    # Руководители — первые в своём отделе и все в главном офисе.
    heads = {}
    for at, code in enumerate(departments):
        heads.setdefault(code, at)
    order = list(range(len(departments)))
    shuffle = rng("offices")
    head_slots = set(heads.values())
    rest = [one for one in order if one not in head_slots]
    shuffle.shuffle(rest)
    office_for = {}
    pool = list(offices)
    for slot in sorted(head_slots):
        pool.remove("HQ")
        office_for[slot] = "HQ"
    for slot, office in zip(rest, pool):
        office_for[slot] = office

    rows = []
    for at, code in enumerate(departments):
        rows.append({"department": code, "office": office_for[at], "head": at in head_slots, "status": "ACTIVE"})

    # Двенадцать стажёров — не руководители, в разных отделах.
    trainee_plan = ["SALES", "SALES", "SALES", "SUPPORT", "SUPPORT", "OPS", "OPS", "IT", "IT", "MKT", "FIN", "PRODUCT"]
    taken = set()
    for code in trainee_plan:
        slot = next(at for at, row in enumerate(rows)
                    if row["department"] == code and not row["head"] and at not in taken and at % 3 == 1)
        taken.add(slot)
        rows[slot]["status"] = "PROBATION"

    # Бывшие сотрудники: десять, в разных отделах и офисах.
    for at in range(10):
        dep = cat.DEPARTMENTS[at % len(cat.DEPARTMENTS)].code
        off = cat.OFFICES[at % len(cat.OFFICES)].code
        rows.append({"department": dep, "office": off, "head": False, "status": "TERMINATED"})
    return rows


def build_people(org, structure: Structure, today: date, reviewer, tz) -> list[Person]:
    rows = plan_people()
    names = _names(len(rows))
    people: list[Person] = []
    trainees = [at for at, row in enumerate(rows) if row["status"] == "PROBATION"]
    active = [at for at, row in enumerate(rows) if row["status"] != "TERMINATED"]

    # Telegram: у 8 из 12 стажёров и у 157 из 178 штатных — живая
    # привязка. Двенадцать непривязанных штатных когда-то были привязаны
    # (привязка отозвана), девять — никогда.
    unlinked_trainees = set(trainees[-4:])
    staff = [at for at in active if at not in trainees]
    unlinked_staff = [at for at in staff if not rows[at]["head"]][-21:]
    revoked = set(unlinked_staff[:12])
    never = set(unlinked_staff[12:]) | unlinked_trainees

    for at, row in enumerate(rows):
        r = rng("person", at)
        female, first, last, middle = names[at]
        number = cat.number(at)
        schedule = next(d.schedule for d in cat.DEPARTMENTS if d.code == row["department"])
        # В каждом отделе с графиком «Пятидневка» часть людей работает поздно.
        if schedule == "OFFICE" and at % 9 == 4:
            schedule = "LATE"
        hire = today - timedelta(days=r.randint(200, 2400))
        telegram = "NONE" if at in never else "REVOKED" if at in revoked else "ACTIVE"
        if row["status"] == "TERMINATED":
            telegram = "REVOKED" if at % 2 else "NONE"
        employee = Employee.objects.create(
            id=did("employee", number), organization=org, employee_number=number,
            first_name=first, last_name=last, middle_name=middle,
            gender="FEMALE" if female else "MALE",
            marital_status=r.choice(("SINGLE", "MARRIED", "MARRIED", "MARRIED")),
            birth_date=today - timedelta(days=r.randint(22 * 365, 55 * 365)),
            hire_date=hire, employment_status=row["status"], preferred_language="ru",
            phone=f"+998 {r.choice((90, 91, 93, 94, 97, 99))} {r.randint(100, 999)} {r.randint(10, 99)} {r.randint(10, 99)}",
            corporate_email=f"{_latin(first)}.{_latin(last)}{at + 1}@{cat.MAIL_DOMAIN}",
            telegram_connected=telegram == "ACTIVE",
        )
        people.append(Person(
            order=at, number=number, employee=employee, office=row["office"], department=row["department"],
            schedule=schedule, status=row["status"], telegram=telegram, head=row["head"], hire=hire,
        ))
    _roles(people, today)
    _dates(people, today)
    _assign(org, structure, people, today)
    _telegram(org, people, today, tz)
    _history(org, people, reviewer, today, tz)
    return people


def _names(count: int) -> list[tuple[bool, str, str, str]]:
    """Имена без повторов пары «фамилия + имя» и без двойников стенда."""
    seen = set(cat.TAKEN)
    result = []
    for at in range(count):
        r = rng("name", at)
        female = r.random() < 0.42
        for _ in range(200):
            first = r.choice(cat.FEMALE_NAMES if female else cat.MALE_NAMES)
            last = r.choice(cat.SURNAMES)[1 if female else 0]
            if (last, first) not in seen:
                break
        seen.add((last, first))
        middle = r.choice(cat.PATRONYMICS)[1 if female else 0]
        result.append((female, first, last, middle))
    return result


def _roles(people: list[Person], today: date) -> None:
    """Роли сценария. Руководители и стажёры в отсутствия не попадают."""
    staff = [p for p in people if p.status == "ACTIVE" and not p.head]
    linked = [p for p in staff if p.telegram == "ACTIVE"]
    plan = [
        ("vacation", 5), ("sick", 3), ("trip", 2), ("no_mark", 3), ("waiting_cert", 1), ("hr_review", 1),
        ("late", 5), ("late_notice", 2), ("geofence", 2), ("stale", 2), ("needs_fix", 1),
        ("vacation_pending", 2), ("vacation_clarify", 1), ("vacation_cancelled", 1), ("trip_pending", 1),
        ("promoted", 3), ("correction", 4), ("moved", 3), ("new_hire", 2),
    ]
    pick = rng("roles")
    pool = list(linked)
    pick.shuffle(pool)
    for role, count in plan:
        for _ in range(count):
            person = pool.pop()
            person.roles.add(role)
    trainees = [p for p in people if p.status == "PROBATION"]
    for at, person in enumerate(trainees):
        person.roles.add("trainee_ending" if at in (2, 5) else "trainee_new" if at in (8, 11) else "trainee")
    terminated = [p for p in people if p.status == "TERMINATED"]
    terminated[0].roles.add("failed")
    terminated[1].roles.add("left_recent")


def _dates(people: list[Person], today: date) -> None:
    """Даты приёма, стажировки и увольнения по ролям."""
    for person in people:
        e = person.employee
        r = rng("dates", person.number)
        if "trainee" in person.roles:
            start = today - timedelta(days=r.randint(12, 55))
            e.hire_date, e.probation_from, e.probation_to = start, start, start + timedelta(days=90)
        elif "trainee_ending" in person.roles:
            end = today + timedelta(days=3 if person.order % 2 else 6)
            e.probation_to, e.probation_from = end, end - timedelta(days=60)
            e.hire_date = e.probation_from
        elif "trainee_new" in person.roles:
            start = today - timedelta(days=3 if person.order % 2 else 8)
            e.hire_date, e.probation_from, e.probation_to = start, start, start + timedelta(days=60)
        elif "promoted" in person.roles:
            end = today - timedelta(days=5 + 5 * (person.order % 3))
            e.probation_to, e.probation_from = end, end - timedelta(days=60)
            e.hire_date = e.probation_from
        elif "new_hire" in person.roles:
            e.hire_date = today - timedelta(days=15 if person.order % 2 else 21)
        elif "failed" in person.roles:
            end = today - timedelta(days=4)
            e.probation_to, e.probation_from = end, end - timedelta(days=60)
            e.hire_date = e.probation_from
            e.termination_date, e.termination_reason = end, PROBATION_FAILED
        elif "left_recent" in person.roles:
            e.termination_date, e.termination_reason = today - timedelta(days=12), "По собственному желанию"
        elif person.status == "TERMINATED":
            e.termination_date = today - timedelta(days=r.randint(45, 300))
            e.termination_reason = r.choice(("По собственному желанию", "Переезд в другой город", "По соглашению сторон"))
            if e.hire_date >= e.termination_date:
                e.hire_date = e.termination_date - timedelta(days=r.randint(200, 900))
        person.hire, person.termination = e.hire_date, e.termination_date
        e.save()


def _assign(org, structure: Structure, people: list[Person], today: date) -> None:
    """Назначения с руководителем, графики и наставники."""
    heads = {p.department: p for p in people if p.head}
    departments = [d.code for d in cat.DEPARTMENTS]
    assignments, schedules = [], []
    for person in people:
        spec = next(d for d in cat.DEPARTMENTS if d.code == person.department)
        r = rng("assign", person.number)
        if person.head:
            title = spec.head
        elif person.status == "PROBATION":
            title = spec.trainee
        else:
            title = spec.positions[r.randrange(len(spec.positions))]
        manager = None if person.head else heads[person.department].employee
        ends = person.termination
        periods = [(person.hire, None, person.department)]
        if "moved" in person.roles:
            moved = today - timedelta(days=6 + 7 * (person.order % 3))
            before = departments[(departments.index(person.department) + 3) % len(departments)]
            person.moved_from, person.moved_on = before, moved
            periods = [(person.hire, moved - timedelta(days=1), before), (moved, None, person.department)]
        for at, (since, until, department) in enumerate(periods):
            assignments.append(EmployeeAssignment(
                id=did("assignment", person.number, at), organization=org, employee=person.employee,
                office=structure.offices[person.office], department=structure.departments[department],
                position=structure.positions[(spec.code, title)] if department == person.department
                else structure.positions[(department, next(d for d in cat.DEPARTMENTS if d.code == department).positions[0])],
                manager_employee=manager if department == person.department else heads[department].employee,
                employment_type="INTERN" if person.status == "PROBATION" else ("PART_TIME" if person.schedule == "SHORT" else "FULL_TIME"),
                work_mode="HYBRID" if spec.code in ("IT", "PRODUCT") and person.order % 4 == 0 else "ONSITE",
                is_primary=True, valid_from=since, valid_to=until if until else ends,
            ))
        schedules.append(EmployeeScheduleAssignment(
            id=did("schedule-assignment", person.number), organization=org, employee=person.employee,
            schedule=structure.schedules[person.schedule], valid_from=person.hire, valid_to=ends,
        ))
    EmployeeAssignment.objects.bulk_create(assignments, batch_size=500)
    EmployeeScheduleAssignment.objects.bulk_create(schedules, batch_size=500)

    for code, head in heads.items():
        Department.objects.filter(id=structure.departments[code].id).update(head_employee=head.employee)

    # Наставник — опытный коллега того же отдела, не руководитель.
    for person in people:
        if not ({"trainee", "trainee_ending", "trainee_new", "promoted", "failed"} & person.roles):
            continue
        mentor = next(
            p for p in people
            if p.department == person.department and p.status == "ACTIVE" and not p.head
            and not p.roles and p.hire < today - timedelta(days=365)
        )
        Employee.objects.filter(id=person.employee.id).update(mentor_employee=mentor.employee)


def _telegram(org, people: list[Person], today: date, tz) -> None:
    rows = []
    for person in people:
        if person.telegram == "NONE":
            continue
        connected = datetime.combine(max(person.hire, today - timedelta(days=400)), time(10, 0), tzinfo=tz)
        chat = DEMO_CHAT_ID_FLOOR + person.order
        rows.append(TelegramAccount(
            id=did("telegram", person.number), organization=org, employee=person.employee,
            telegram_user_id=chat, telegram_chat_id=chat, telegram_username=None, language_code="ru",
            status="ACTIVE" if person.telegram == "ACTIVE" else "REVOKED", connected_at=connected,
            revoked_at=None if person.telegram == "ACTIVE" else connected + timedelta(days=120),
        ))
    TelegramAccount.objects.bulk_create(rows, batch_size=500)


def _history(org, people: list[Person], reviewer, today: date, tz) -> None:
    """Журнал карточки: приём, переводы, перевод в штат, увольнение."""
    rows = []

    def at(day: date, hour: int = 10) -> datetime:
        return datetime.combine(day, time(hour, 0), tzinfo=tz)

    for person in people:
        e = person.employee
        rows.append(AuditLog(
            id=did("audit", person.number, "create"), organization=org, actor_user=reviewer,
            action="employee.create", entity_type="employees", entity_id=e.id,
            new_values={"employee_number": e.employee_number, "hire_date": str(e.hire_date),
                        "employment_status": "PROBATION" if e.probation_from else "ACTIVE"},
            occurred_at=at(person.hire - timedelta(days=3)),
        ))
        if person.moved_on:
            rows.append(AuditLog(
                id=did("audit", person.number, "move"), organization=org, actor_user=reviewer,
                action="employee.assignment.change", entity_type="employees", entity_id=e.id,
                old_values={"department": person.moved_from}, new_values={"department": person.department,
                                                                          "valid_from": str(person.moved_on)},
                occurred_at=at(person.moved_on - timedelta(days=2)),
            ))
        if "promoted" in person.roles:
            rows.append(AuditLog(
                id=did("audit", person.number, "promote"), organization=org, actor_user=reviewer,
                action="employee.promote", entity_type="employees", entity_id=e.id,
                old_values={"employment_status": "PROBATION"}, new_values={"employment_status": "ACTIVE"},
                occurred_at=at(e.probation_to, 15),
            ))
        if person.termination:
            rows.append(AuditLog(
                id=did("audit", person.number, "terminate"), organization=org, actor_user=reviewer,
                action="employee.terminate", entity_type="employees", entity_id=e.id,
                old_values={"employment_status": "ACTIVE"},
                new_values={"employment_status": "TERMINATED", "termination_date": str(person.termination),
                            "termination_reason": e.termination_reason},
                occurred_at=at(person.termination, 17),
            ))
    AuditLog.objects.bulk_create(rows, batch_size=500)


_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def _latin(text: str) -> str:
    return "".join(_LATIN.get(ch, ch) for ch in text.lower())
