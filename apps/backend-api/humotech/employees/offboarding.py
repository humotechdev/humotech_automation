"""Увольнение: граница дня, закрытие доступа, защита последнего администратора.

Решения владельца (ТЗ 12.10, `q2-offboarding-decisions.md`):

* **В1.** При увольнении будущей датой сотрудник остаётся действующим до
  конца последнего дня: в смене, аналитике, боте, Mini App. Статус меняет
  ежедневная задача `apply_due_terminations` после границы.
* **В10.** Граница — конец последнего дня (00:00 следующего) по поясу
  организации. Если пояс офиса сотрудника другой, это конфликт: он
  записывается в журнал и вынесен владельцу, молча не решается.
* **В2 / В3.** Роли учётной записи CRM, привязанной к сотруднику, закрываются
  этой границей (`valid_to`), строки ролей остаются — история «кто и до
  какого дня имел доступ». Сессии CRM удаляются. Учётка без карточки
  сотрудника (техническая) не затрагивается.
* **В5.** Закрытые роли не оживают: повторный приём — новое решение о доступе.
* **В8.** Нельзя уволить последнего действующего главного администратора всей
  организации: проверка при оформлении и ещё раз в момент срабатывания.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from humotech.core.errors import Conflict
from humotech.core.timeframes import day_bounds, office_zone, organization_zone

logger = logging.getLogger(__name__)

SUPER_ADMIN = "SUPER_ADMIN"
GONE_STATUSES = ("TERMINATED", "ARCHIVED")


def boundary(organization_id, day: date) -> datetime:
    """Конец последнего рабочего дня — начало следующих суток, пояс организации."""
    return day_bounds(day, organization_zone(organization_id))[1]


def timezone_conflict(employee) -> dict | None:
    """Пояс офиса сотрудника отличается от пояса организации (вопрос В10)."""
    from humotech.employees.models import EmployeeAssignment

    org_tz = organization_zone(employee.organization_id)
    assignment = (
        EmployeeAssignment.objects.filter(employee_id=employee.id, is_primary=True)
        .select_related("office", "office__organization").order_by("-valid_from").first()
    )
    if assignment is None or assignment.office is None:
        return None
    office_tz = office_zone(assignment.office)
    if str(office_tz) == str(org_tz):
        return None
    return {"organization": str(org_tz), "office": str(office_tz)}


def refuse_if_last_super_admin(employee) -> None:
    """Не дать организации остаться без главного администратора (В8).

    Считаются только живые назначения SUPER_ADMIN на ВСЮ организацию у
    действующих, не архивных учёток, не привязанных к уволенным. Строка роли
    блокируется — два одновременных увольнения не проскочат оба.
    """
    from humotech.accounts.models import UserRoleScope
    from humotech.rbac.models import Role

    list(Role.objects.filter(
        Q(organization_id=employee.organization_id) | Q(organization__isnull=True),
        code=SUPER_ADMIN,
    ).select_for_update())
    moment = timezone.now()
    live = (
        UserRoleScope.objects.filter(
            organization_id=employee.organization_id, role__code=SUPER_ADMIN,
            region__isnull=True, office__isnull=True,
            user__status="ACTIVE", user__archived_at__isnull=True,
            valid_from__lte=moment,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=moment))
        .exclude(user__employee__employment_status__in=GONE_STATUSES)
    )
    if not live.filter(user__employee_id=employee.id).exists():
        return  # уволенный — не главный администратор, терять нечего
    if not live.exclude(user__employee_id=employee.id).exists():
        raise Conflict(
            "Сотрудник — последний главный администратор организации. "
            "Назначьте другого, прежде чем увольнять этого.",
            details={"role": SUPER_ADMIN, "reason": "last_super_admin"},
        )


def close_access(employee, at: datetime) -> dict:
    """Закрыть роли и сессии учёток CRM, привязанных к сотруднику (В2, В3)."""
    from humotech.accounts.models import User, UserRoleScope
    from humotech.accounts.security import end_sessions

    closed_roles = 0
    ended_sessions = 0
    stop = at - timedelta(seconds=1)
    for user in User.objects.filter(employee_id=employee.id):
        for grant in UserRoleScope.objects.select_for_update().filter(user_id=user.id).filter(
            Q(valid_to__isnull=True) | Q(valid_to__gt=stop)
        ):
            grant.valid_to = max(stop, grant.valid_from)
            grant.save(update_fields=["valid_to"])
            closed_roles += 1
        ended_sessions += end_sessions(user.id)
    return {"closed_roles": closed_roles, "ended_sessions": ended_sessions}


def apply_due_terminations(*, now: datetime | None = None, dry_run: bool = False) -> int:
    """Перевести в «уволен» тех, у кого последний день уже кончился (В1).

    Идемпотентна: берутся только ещё не уволенные. Перед закрытием доступа
    защита последнего администратора проверяется ещё раз (В8): если замену
    успели снять, увольнение ждёт и попадает в журнал.
    """
    from humotech.employees.models import Employee

    moment = now or timezone.now()
    due = [
        person for person in Employee.objects.filter(
            termination_date__isnull=False,
            employment_status__in=("ACTIVE", "SUSPENDED"),
        )
        if moment >= boundary(person.organization_id, person.termination_date)
    ]
    if dry_run:
        return len(due)
    applied = 0
    for person in due:
        try:
            with transaction.atomic():
                applied += _apply_one(person.id, moment)
        except Exception:  # noqa: BLE001 — один сотрудник не останавливает остальных
            logger.exception("termination apply failed for %s", person.id)
    return applied


def _apply_one(employee_id: uuid.UUID, moment: datetime) -> int:
    from humotech.absences.services import lock_employee
    from humotech.core.rbac import AuditTrail, snapshot
    from humotech.employees.models import Employee

    lock_employee(employee_id)
    person = Employee.objects.select_for_update().filter(
        id=employee_id, employment_status__in=("ACTIVE", "SUSPENDED")).first()
    if person is None or person.termination_date is None:
        return 0
    edge = boundary(person.organization_id, person.termination_date)
    if moment < edge:
        return 0
    audit = AuditTrail()
    try:
        refuse_if_last_super_admin(person)
    except Conflict:
        audit.record_system(
            organization_id=person.organization_id, action="employee.termination.blocked",
            entity_type="employees", entity_id=person.id,
            before=None, after={"reason": "last_super_admin",
                                "termination_date": str(person.termination_date)},
        )
        return 0
    fields = ("employment_status", "termination_date")
    before = snapshot(person, fields)
    person.employment_status = "TERMINATED"
    person.save(update_fields=["employment_status", "updated_at"])
    access = close_access(person, edge)
    audit.record_system(
        organization_id=person.organization_id, action="employee.termination.applied",
        entity_type="employees", entity_id=person.id,
        before=before, after={**snapshot(person, fields), **access},
    )
    return 1
