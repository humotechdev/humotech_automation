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

from django.db.models import Q
from django.utils import timezone

from humotech.core.errors import NotFound, PermissionDenied
from humotech.core.rbac import AccessControl, Actor
from humotech.employees.models import Employee, EmployeeAssignment


def require_visible_employee(
    access: AccessControl, actor: Actor, employee_id: uuid.UUID,
    permission: str | None = None,
) -> Employee:
    """Сотрудник существует, он из организации актора и попадает в его область.

    `access` передаётся снаружи, а не создаётся здесь: у вызывающего сервиса
    уже есть свой экземпляр с прогретым кэшем разрешений, и заводить второй
    значило бы перечитывать роли на каждую проверку.

    `permission` задаёт разрез области: для ИЗМЕНЕНИЯ карточки сотрудник
    должен быть виден через назначение, несущее это право, а не через
    соседнее «только чтение». Без него — прежняя общая область (чтение).
    """
    employee = Employee.objects.filter(
        id=employee_id, organization_id=actor.organization_id
    ).first()
    if employee is None:
        # чужая организация отвечает так же, как отсутствие записи
        raise NotFound("Сотрудник не найден")

    visible = access.visible_office_ids(actor, permission)
    if visible is None:
        return employee

    if not _covers(employee_id, visible, permission):
        raise PermissionDenied("Сотрудник вне вашей области видимости")
    return employee


def _covers(employee_id: uuid.UUID, visible: set, permission: str | None) -> bool:
    """Попадает ли сотрудник в область `visible` для этого права.

    ЧТЕНИЕ (`permission` не задан или `*.read`) смотрит любой период: у
    сотрудника мог смениться офис, а увольнение закрывает все периоды —
    и HR своего региона потерял бы доступ к карточкам своих уволенных.

    ЗАПИСЬ и документы — только по офисам, за которые сотрудник отвечает
    сейчас: действующие и будущие назначения, а у кого их нет (уволен,
    все периоды закрыты) — последнее. Иначе HR прежнего офиса навсегда
    сохранял бы право править, увольнять и открывать паспорт
    переведённого сотрудника (аудит 11.10, M-6).
    """
    rows = EmployeeAssignment.objects.filter(employee_id=employee_id)
    if permission is None or permission.endswith(".read"):
        return rows.filter(office_id__in=visible).exists()

    today = timezone.localdate()
    live = rows.filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
    offices = set(live.values_list("office_id", flat=True))
    if not offices:
        last = rows.order_by("-valid_from", "-created_at").values_list(
            "office_id", flat=True).first()
        offices = {last} if last else set()
    return bool(offices & set(visible))
