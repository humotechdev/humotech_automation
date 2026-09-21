"""Движение сотрудников: сколько пришло, сколько ушло, что осталось.

Отдельно от посещаемости намеренно. Посещаемость отвечает на вопрос
«кто был на месте», движение — на вопрос «сколько нас и куда это идёт».
Это разные вопросы с разной единицей измерения: там дни, здесь люди.

**Приём считается по дате выхода, увольнение — по дате увольнения.**
Не по дате создания карточки: человека оформляют заранее, и приём,
посчитанный по карточке, показал бы всплеск в день, когда кадровик
сел за компьютер.

**Сравнение — с равным периодом, а не с календарным.** Неделя
сравнивается с предыдущей неделей той же длины, а не «с прошлой
календарной». Иначе период в десять дней сравнивался бы с месяцем, и
разница объяснялась бы длиной, а не событиями.

**Год к году считается сдвигом на год, а не вычитанием 365 дней.**
В високосный год сдвиг на 365 дней уезжает на сутки, и март
сравнивался бы с концом февраля.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Q

from humotech.core.rbac import Actor
from humotech.core.service import BaseService
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.services import WORKING_STATUSES


@dataclass(frozen=True)
class Movement:
    """Итоги одного периода."""

    first: date
    last: date
    hired: int
    left: int

    @property
    def difference(self) -> int:
        """Чистое изменение. Отрицательное — людей стало меньше."""
        return self.hired - self.left


@dataclass(frozen=True)
class MovementReport:
    current: Movement
    #: Столько же дней непосредственно перед периодом.
    previous: Movement
    #: Тот же период месяцем раньше.
    month_before: Movement
    #: Тот же период годом раньше.
    year_before: Movement
    #: Сколько человек числится на конец периода.
    headcount: int


class MovementService(BaseService):
    """Приём и увольнение по периодам, с оглядкой назад."""

    def report(
        self,
        actor: Actor,
        *,
        first: date,
        last: date,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
    ) -> MovementReport:
        self.access.require(actor, "employees.read")
        if last < first:
            first, last = last, first

        days = (last - first).days + 1
        scope = self._scope(actor, office_id=office_id, region_id=region_id)

        return MovementReport(
            current=self._count(actor, first, last, scope),
            previous=self._count(
                actor, first - timedelta(days=days), first - timedelta(days=1), scope
            ),
            month_before=self._count(
                actor, _months_back(first, 1), _months_back(last, 1), scope
            ),
            year_before=self._count(
                actor, _years_back(first, 1), _years_back(last, 1), scope
            ),
            headcount=self._headcount(actor, last, scope),
        )

    # ------------------------------------------------------------ внутреннее

    def _count(self, actor: Actor, first: date, last: date, scope) -> Movement:
        people = Employee.objects.filter(organization_id=actor.organization_id)
        if scope is not None:
            people = people.filter(id__in=scope)

        return Movement(
            first=first,
            last=last,
            hired=people.filter(hire_date__gte=first, hire_date__lte=last).count(),
            left=people.filter(
                termination_date__gte=first, termination_date__lte=last
            ).count(),
        )

    def _headcount(self, actor: Actor, at: date, scope) -> int:
        """Сколько человек числится на дату.

        Считаются стажёры и штатные: стажёр — работающий человек, и не
        видеть его в численности значит отчитываться не тем числом,
        которое стоит в коридоре.
        """
        people = Employee.objects.filter(
            organization_id=actor.organization_id,
            employment_status__in=WORKING_STATUSES,
            hire_date__lte=at,
        ).filter(Q(termination_date__isnull=True) | Q(termination_date__gt=at))
        if scope is not None:
            people = people.filter(id__in=scope)
        return people.count()

    def _scope(self, actor: Actor, *, office_id, region_id):
        """Идентификаторы людей в области видимости. `None` — вся организация.

        Именно `None` для «всех» и ПУСТОЙ набор для «ничего не видно»:
        слить эти случаи проверкой `if ids:` значит молча показать всю
        организацию тому, у кого прав нет.
        """
        condition = None
        if office_id is not None:
            self.access.require_office(actor, office_id)
            condition = Q(office_id=office_id)
        elif region_id is not None:
            self.access.require_region(actor, region_id)
            condition = Q(office__region_id=region_id)
        else:
            visible = self.access.visible_office_ids(actor)
            if visible is not None:
                condition = Q(office_id__in=visible)

        if condition is None:
            return None
        return EmployeeAssignment.objects.filter(
            condition, is_primary=True
        ).values_list("employee_id", flat=True)


def _months_back(value: date, months: int) -> date:
    """Та же дата месяцем раньше. 31 марта → 28 (или 29) февраля."""
    month = value.month - months
    year = value.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(value.day, _days_in(year, month))
    return date(year, month, day)


def _years_back(value: date, years: int) -> date:
    """Та же дата годом раньше. 29 февраля → 28 февраля."""
    year = value.year - years
    day = min(value.day, _days_in(year, value.month))
    return date(year, value.month, day)


def _days_in(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


__all__ = ["Movement", "MovementReport", "MovementService"]
