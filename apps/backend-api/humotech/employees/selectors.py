"""Выборки по сотрудникам, общие для нескольких сервисов.

Здесь живёт одно правило — «виден ли сотрудник этому пользователю». Оно
вынесено из `EmployeeService` не ради красоты: тем же правилом пользуется
привязка Telegram, и разойтись эти два ответа не имеют права. Если бы
кадровый сервис и телеграмный проверяли область по-разному, HR своего региона
мог бы выдать ссылку на привязку сотруднику чужого офиса — и заметить это
было бы нечем.
"""

from __future__ import annotations

import uuid

from humotech.core.errors import NotFound, PermissionDenied
from humotech.core.rbac import AccessControl, Actor
from humotech.employees.models import Employee, EmployeeAssignment


def require_visible_employee(
    access: AccessControl, actor: Actor, employee_id: uuid.UUID
) -> Employee:
    """Сотрудник существует, он из организации актора и попадает в его область.

    `access` передаётся снаружи, а не создаётся здесь: у вызывающего сервиса
    уже есть свой экземпляр с прогретым кэшем разрешений, и заводить второй
    значило бы перечитывать роли на каждую проверку.
    """
    employee = Employee.objects.filter(
        id=employee_id, organization_id=actor.organization_id
    ).first()
    if employee is None:
        # чужая организация отвечает так же, как отсутствие записи
        raise NotFound("Сотрудник не найден")

    visible = access.visible_office_ids(actor)
    if visible is None:
        return employee

    # У сотрудника мог смениться офис, поэтому проверяется не только
    # текущий период, но и любой, попадающий в область видимости.
    # Сузить до текущего нельзя: увольнение закрывает все периоды,
    # и HR своего же региона потерял бы доступ к карточкам своих уволенных.
    allowed = EmployeeAssignment.objects.filter(
        employee_id=employee_id, office_id__in=visible
    ).exists()
    if not allowed:
        raise PermissionDenied("Сотрудник вне вашей области видимости")
    return employee
