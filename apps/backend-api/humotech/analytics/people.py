"""Люди за период: численность, приём, уход, переводы, стажировки.

Посещаемость отвечает на вопрос «кто был на месте» и считает дни.
Здесь единица — человек: сколько нас было, кто пришёл, кто ушёл,
кого перевели и кто сейчас на стажировке.

**Численность на дату** — принят не позже этой даты, не уволен к ней и
в этот день числится основным назначением в выбранных офисах. Статус
«сейчас» здесь не годится: уволенный сегодня человек месяц назад был
в штате, и график численности обязан это показать.

**Приём — по дате выхода, уход — по дате увольнения**, как в
`movement`: не по дате, когда карточку завели.

**Оставлен после стажировки** — запись журнала `employee.promote`.
Отдельного поля «дата перевода в штат» нет, и выдумывать её по
косвенным признакам значит ошибаться там, где карточку правили руками.

**Не прошёл стажировку** — увольнение с причиной `PROBATION_FAILED`:
отдельного статуса для этого нет, так и оформляет кнопка в карточке.

**Перевод** — новое основное назначение, у которого отдел или офис
отличается от предыдущего. Смена одной должности переводом не
считается: это повышение или уточнение, и в «переводах» оно было бы
шумом.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone as django_timezone

from humotech.analytics.metrics import AnalyticsService, _validate_period
from humotech.audit.models import AuditLog
from humotech.core.rbac import Actor
from humotech.core.timeframes import days_in, office_zone
from humotech.core.timeframes import today as local_today
from humotech.employees.lifecycle import PROBATION_FAILED
from humotech.employees.models import Employee, EmployeeAssignment

#: Сколько дней вперёд решение по стажировке считается «скоро».
DUE_DAYS = 7


class PeopleService(AnalyticsService):
    """«Команда» и «Стажировки» страницы аналитики."""

    # ---------------------------------------------------------------- команда

    def team(
        self,
        actor: Actor,
        *,
        first: date,
        last: date,
        region_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
    ) -> dict:
        self.access.require(actor, "analytics.read")
        _validate_period(first, last)
        offices = self._scope_offices(actor, region_id=region_id, office_id=office_id)
        length = (last - first).days + 1
        previous_last = first - timedelta(days=1)
        previous_first = previous_last - timedelta(days=length - 1)

        spans = self._spans(actor, offices, previous_first - timedelta(days=1), last, department_id)
        people = {
            row.id: row for row in Employee.objects.filter(
                id__in={span[0] for span in spans}
            ).only(
                "id", "first_name", "last_name", "employee_number", "hire_date",
                "termination_date", "termination_reason", "employment_status",
                "probation_to",
            )
        }
        names = _names(spans)
        index = _by_person(spans)

        def headcount(day: date) -> int:
            return len(_present(spans, people, day))

        def counted(one: date, two: date) -> dict:
            inside = _members(spans, one, two)
            hired = [p for p in inside if one <= people[p].hire_date <= two]
            left = [
                p for p in inside
                if people[p].termination_date and one <= people[p].termination_date <= two
            ]
            return {
                "hired": hired,
                "left": left,
                "failed": [p for p in left if people[p].termination_reason == PROBATION_FAILED],
                "promoted": self._promoted(actor, inside, one, two),
            }

        now = counted(first, last)
        before = counted(previous_first, previous_last)
        start = headcount(previous_last)
        end = headcount(last)

        # Численность по дням. Будущие дни не считаются: «сколько нас
        # будет» — не то, о чём этот график.
        today = django_timezone.localdate()
        series = [
            {"day": day.isoformat(), "headcount": headcount(day)}
            for day in days_in(first, min(last, today))
        ] if first <= today else []

        # Изменения по отделам: у ушедшего — отдел, где он был в день
        # ухода; у пришедшего — где он в день выхода.
        changes: dict = defaultdict(lambda: {"hired": 0, "left": 0})
        for p in now["hired"]:
            changes[_place_at(index, p, people[p].hire_date, "department")]["hired"] += 1
        for p in now["left"]:
            changes[_place_at(index, p, people[p].termination_date, "department")]["left"] += 1
        by_department = sorted(
            (
                {
                    "id": str(key) if key else None,
                    "name": names["department"].get(key, "Без отдела"),
                    "hired": value["hired"],
                    "left": value["left"],
                    "difference": value["hired"] - value["left"],
                }
                for key, value in changes.items()
            ),
            key=lambda row: (-abs(row["difference"]), -row["hired"], row["name"]),
        )

        present_now = _present(spans, people, last)
        present_before = _present(spans, people, previous_last)

        def composition(kind: str) -> list[dict]:
            here = Counter(_place_at(index, p, last, kind) for p in present_now)
            was = Counter(_place_at(index, p, previous_last, kind) for p in present_before)
            rows = [
                {
                    "id": str(key) if key else None,
                    "name": names[kind].get(key, "Без отдела" if kind == "department" else "—"),
                    "headcount": here.get(key, 0),
                    "previous_headcount": was.get(key, 0),
                }
                for key in set(here) | set(was)
            ]
            rows.sort(key=lambda row: (-row["headcount"], row["name"]))
            return rows

        return {
            "period": {"first": first.isoformat(), "last": last.isoformat(), "days": length},
            "previous_period": {"first": previous_first.isoformat(), "last": previous_last.isoformat()},
            "summary": {
                "headcount": end,
                "headcount_start": start,
                "previous_headcount_start": headcount(previous_first - timedelta(days=1)),
                "hired": len(now["hired"]),
                "previous_hired": len(before["hired"]),
                "promoted": len(now["promoted"]),
                "previous_promoted": len(before["promoted"]),
                "left": len(now["left"]),
                "previous_left": len(before["left"]),
                "probation_failed": len(now["failed"]),
            },
            "series": series,
            "by_department": by_department,
            "departments": composition("department"),
            "offices": composition("office"),
            "hires": sorted(
                (self._person(people[p], index, names, people[p].hire_date) for p in now["hired"]),
                key=lambda row: (row["hire_date"], row["name"]), reverse=True,
            ),
            "departures": sorted(
                (
                    {
                        **self._person(people[p], index, names, people[p].termination_date),
                        "termination_date": people[p].termination_date.isoformat(),
                        "reason": people[p].termination_reason,
                    }
                    for p in now["left"]
                ),
                key=lambda row: row["termination_date"], reverse=True,
            ),
            "transfers": _transfers(spans, names, people, first, last),
        }

    # ------------------------------------------------------------ стажировки

    def probation(
        self,
        actor: Actor,
        *,
        first: date,
        last: date,
        region_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
    ) -> dict:
        self.access.require(actor, "analytics.read")
        _validate_period(first, last)
        offices = self._scope_offices(actor, region_id=region_id, office_id=office_id)
        length = (last - first).days + 1
        previous_last = first - timedelta(days=1)
        previous_first = previous_last - timedelta(days=length - 1)
        today = django_timezone.localdate()
        zones = {office.id: office_zone(office) for office in offices}

        spans = self._spans(actor, offices, previous_first, max(last, today), department_id)
        names = _names(spans)
        index = _by_person(spans)
        people = {
            row.id: row for row in Employee.objects.filter(id__in={span[0] for span in spans})
        }

        mentors = {
            one.id: one for one in Employee.objects.filter(
                id__in={p.mentor_employee_id for p in people.values() if p.mentor_employee_id}
            ).only("id", "first_name", "last_name")
        }

        # Сейчас на стажировке — по статусу и текущему назначению в выборке.
        trainees = []
        for p, person in people.items():
            if person.employment_status != "PROBATION":
                continue
            office = _place_at(index, p, today, "office")
            if office is None:
                continue
            here = local_today(zones[office]) if office in zones else today
            left = (person.probation_to - here).days if person.probation_to else None
            mentor = mentors.get(person.mentor_employee_id)
            trainees.append({
                **self._person(person, index, names, today),
                "mentor": {"id": str(mentor.id), "name": f"{mentor.last_name} {mentor.first_name}"} if mentor else None,
                "probation_from": (person.probation_from or person.hire_date).isoformat(),
                "probation_to": person.probation_to.isoformat() if person.probation_to else None,
                "days_left": left,
                "days_total": (
                    (person.probation_to - (person.probation_from or person.hire_date)).days + 1
                    if person.probation_to else None
                ),
            })
        # Ближайшее решение сверху; без даты окончания — в конце.
        trainees.sort(key=lambda row: (row["days_left"] is None, row["days_left"] or 0, row["name"]))

        def funnel(one: date, two: date) -> dict:
            inside = _members(spans, one, two)
            # Начало стажировки — как в строке стажёра: без отдельной даты
            # стажировка начинается с выхода на работу. Только у тех, кто
            # на стажировке был: штатный с датой выхода в периоде — не стажёр.
            started = [
                p for p in inside
                if (people[p].probation_from or people[p].probation_to or people[p].employment_status == "PROBATION")
                and one <= (people[p].probation_from or people[p].hire_date) <= two
            ]
            promoted = self._promoted(actor, inside, one, two)
            failed = [
                p for p in inside
                if people[p].termination_reason == PROBATION_FAILED
                and people[p].termination_date and one <= people[p].termination_date <= two
            ]
            return {"started": len(started), "promoted": len(promoted), "failed": len(failed)}

        now = funnel(first, last)
        before = funnel(previous_first, previous_last)
        decided = now["promoted"] + now["failed"]
        due = [row for row in trainees if row["days_left"] is not None and row["days_left"] <= DUE_DAYS]

        return {
            "period": {"first": first.isoformat(), "last": last.isoformat(), "days": length},
            "previous_period": {"first": previous_first.isoformat(), "last": previous_last.isoformat()},
            "summary": {
                "active": len(trainees),
                "due": len(due),
                "overdue": sum(1 for row in due if row["days_left"] < 0),
                "started": now["started"],
                "promoted": now["promoted"],
                "failed": now["failed"],
                "previous_started": before["started"],
                "previous_promoted": before["promoted"],
                "previous_failed": before["failed"],
                # Доля оставленных среди решённых за период. Нет решений —
                # нет и доли: null, а не ноль.
                "conversion_percent": round(now["promoted"] * 100 / decided, 1) if decided else None,
                "previous_conversion_percent": (
                    round(before["promoted"] * 100 / (before["promoted"] + before["failed"]), 1)
                    if before["promoted"] + before["failed"] else None
                ),
            },
            "due_days": DUE_DAYS,
            "trainees": trainees,
        }

    # ------------------------------------------------------------ внутреннее

    def _scope_offices(self, actor: Actor, *, region_id, office_id) -> list:
        if office_id:
            return [self.access.require_office(actor, office_id)]
        if region_id:
            self.access.require_region(actor, region_id)
        return self._offices(actor, region_id=region_id)

    @staticmethod
    def _spans(actor: Actor, offices: list, first: date, last: date, department_id) -> list[tuple]:
        """Основные назначения, задевающие период, в выбранных офисах.

        Кортеж: (сотрудник, с, по, офис, отдел, должность, названия).
        """
        if not offices:
            return []
        rows = EmployeeAssignment.objects.filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=first),
            is_primary=True,
            valid_from__lte=last,
            office_id__in=[office.id for office in offices],
            employee__organization_id=actor.organization_id,
        )
        if department_id is not None:
            rows = rows.filter(department_id=department_id)
        return [
            (
                row.employee_id, row.valid_from, row.valid_to, row.office_id,
                row.department_id, row.position_id,
                (
                    row.office.name,
                    row.department.name if row.department else None,
                    row.position.name if row.position else None,
                ),
            )
            for row in rows.select_related("office", "department", "position")
        ]

    @staticmethod
    def _promoted(actor: Actor, inside: set, first: date, last: date) -> list:
        if not inside:
            return []
        rows = AuditLog.objects.filter(
            organization_id=actor.organization_id,
            action="employee.promote",
            entity_id__in=inside,
        ).values_list("entity_id", "occurred_at")
        seen = set()
        for entity_id, moment in rows:
            if first <= django_timezone.localdate(moment) <= last:
                seen.add(entity_id)
        return list(seen)

    @staticmethod
    def _person(person: Employee, index: dict, names: dict, day: date) -> dict:
        span = _span_at(index, person.id, day)
        return {
            "id": str(person.id),
            "name": " ".join(one for one in [person.last_name, person.first_name] if one),
            "employee_number": person.employee_number,
            "status": person.employment_status,
            "hire_date": person.hire_date.isoformat(),
            "office": span[6][0] if span else None,
            "department": span[6][1] if span else None,
            "position": span[6][2] if span else None,
        }


def _by_person(spans: list) -> dict:
    own: dict = defaultdict(list)
    for span in spans:
        own[span[0]].append(span)
    for items in own.values():
        items.sort(key=lambda s: s[1])
    return own


def _span_at(index: dict, employee_id, day: date):
    """Назначение человека на дату; до первого — первое, после последнего — последнее."""
    own = index.get(employee_id, [])
    for span in own:
        if span[1] <= day and (span[2] is None or span[2] >= day):
            return span
    if not own:
        return None
    return own[0] if day < own[0][1] else own[-1]


def _place_at(index: dict, employee_id, day: date, kind: str):
    span = _span_at(index, employee_id, day)
    if span is None:
        return None
    return span[3] if kind == "office" else span[4]


def _names(spans: list) -> dict:
    result: dict = {"office": {}, "department": {}}
    for span in spans:
        result["office"][span[3]] = span[6][0]
        if span[4] is not None:
            result["department"][span[4]] = span[6][1]
    return result


def _members(spans: list, first: date, last: date) -> set:
    """Кто хоть день периода числился в выборке."""
    return {
        span[0] for span in spans
        if span[1] <= last and (span[2] is None or span[2] >= first)
    }


def _present(spans: list, people: dict, day: date) -> set:
    """Численность на дату: принят, не уволен и числится в выборке."""
    result = set()
    for span in spans:
        if span[1] > day or (span[2] is not None and span[2] < day):
            continue
        person = people.get(span[0])
        if person is None or person.hire_date > day:
            continue
        if person.termination_date is not None and person.termination_date <= day:
            continue
        result.add(span[0])
    return result


def _transfers(spans: list, names: dict, people: dict, first: date, last: date) -> list[dict]:
    own: dict = defaultdict(list)
    for span in spans:
        own[span[0]].append(span)
    rows = []
    for employee_id, items in own.items():
        items.sort(key=lambda s: s[1])
        for before, after in zip(items, items[1:]):
            if not first <= after[1] <= last:
                continue
            if before[3] == after[3] and before[4] == after[4]:
                continue
            person = people.get(employee_id)
            rows.append({
                "id": str(employee_id),
                "name": " ".join(one for one in [person.last_name, person.first_name] if one) if person else "",
                "date": after[1].isoformat(),
                "from_office": before[6][0],
                "to_office": after[6][0],
                "from_department": before[6][1],
                "to_department": after[6][1],
            })
    rows.sort(key=lambda row: row["date"], reverse=True)
    return rows


__all__ = ["PeopleService", "DUE_DAYS"]
