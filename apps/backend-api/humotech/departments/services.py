"""Справочники отделов и должностей.

Оба — плоские справочники без собственной логики, поэтому лежат в одном
модуле: заводить два почти одинаковых файла ради симметрии каталогов
незачем.

Отдел и должность принадлежат компании целиком: «Продажи» и «Инженер»
одни на все города. Заводить их в каждом офисе заново значило бы
получить пять разных отделов продаж в одном отчёте.

У отдела при этом МОЖЕТ быть офис — для подразделения, которое
существует в одном месте. Тогда оно видно только тем, кому виден его
офис; общий отдел виден всем.

Ни то ни другое не удаляется. На отдел и должность ссылаются назначения
сотрудников, в том числе закрытые: удаление стёрло бы ответ на вопрос
«кем человек работал в прошлом году». Есть только перевод в `INACTIVE`.
"""

from __future__ import annotations

import uuid

from django.db.models import Count, Q
from django.utils import timezone

from humotech.core.enums import DEPARTMENT_STATUSES, POSITION_STATUSES
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_code, clean_text
from humotech.departments.models import Department
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.positions.models import Position

DEPARTMENT_FIELDS = ("code", "name", "description", "head_employee_id", "status")
POSITION_FIELDS = ("code", "name", "description", "status")


def headcount(department_id: uuid.UUID) -> int:
    """Сколько человек числится в отделе сегодня.

    Считаются действующие назначения: закрытые говорят, кем человек был
    раньше, и запрещать архив из-за них значило бы держать отдел вечно.
    """
    today = timezone.localdate()
    return (
        EmployeeAssignment.objects.filter(
            department_id=department_id, valid_from__lte=today
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        .values("employee_id")
        .distinct()
        .count()
    )


def headcounts(department_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """То же для списка: одним запросом вместо запроса на строку."""
    today = timezone.localdate()
    rows = (
        EmployeeAssignment.objects.filter(
            department_id__in=department_ids, valid_from__lte=today
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        .values("department_id")
        .annotate(staff=Count("employee_id", distinct=True))
    )
    return {row["department_id"]: row["staff"] for row in rows}


def position_usage(position_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Сколько человек занимают должность сегодня."""
    today = timezone.localdate()
    rows = (
        EmployeeAssignment.objects.filter(
            position_id__in=position_ids, valid_from__lte=today
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        .values("position_id")
        .annotate(staff=Count("employee_id", distinct=True))
    )
    return {row["position_id"]: row["staff"] for row in rows}


def _free_code(prefix: str, taken: set[str]) -> str:
    """Свободный код справочника: префикс и порядковый номер.

    Код нужен базе — по нему стоит уникальный ключ и на него ссылаются
    выгрузки. Человеку он не нужен: в интерфейсе отдел и должность
    опознаются названием. Поэтому код здесь придумывает сервер, а форма
    о нём не спрашивает.
    """
    number = len(taken) + 1
    while f"{prefix}{number}" in taken:
        number += 1
    return f"{prefix}{number}"


class DepartmentService(BaseService):
    """Отделы компании.

    Чтение — по `offices.read`: отдельного разрешения на просмотр
    справочника в каталоге нет. Отдел с офисом виден тому, кому виден
    его офис; общий — всем. Изменение — по `departments.manage`.
    """

    def list(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        search: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "offices.read")

        queryset = Department.objects.filter(
            organization_id=actor.organization_id
        ).select_related("office", "parent_department")

        if office_id:
            self.access.require_office(actor, office_id)
            # Отбор по офису возвращает и ОБЩИЕ отделы: они существуют во
            # всех офисах сразу. Без этого форма приёма сотрудника
            # показывала бы пустой список — «Продажи» есть, но офиса у
            # них нет, и фильтр по офису их не находил.
            queryset = queryset.filter(
                Q(office_id=office_id) | Q(office__isnull=True)
            )
        elif region_id:
            self.access.require_region(actor, region_id)
            queryset = queryset.filter(
                Q(office__region_id=region_id) | Q(office__isnull=True)
            )
        else:
            visible = self.access.visible_office_ids(actor)
            if visible is not None:
                # Общий отдел офиса не имеет и виден всем: ограничение
                # области — про места, а «Продажи» не в месте.
                queryset = queryset.filter(
                    Q(office_id__in=visible) | Q(office__isnull=True)
                )

        if status:
            queryset = queryset.filter(status=status)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(name__icontains=pattern) | queryset.filter(
                code__icontains=pattern
            )
        page = paginate(
            queryset.select_related("head_employee"), limit=limit, cursor=cursor
        )
        # Численность — часть ответа, а не отдельный запрос на строку:
        # без неё список отделов не отвечает на вопрос, можно ли отдел
        # архивировать.
        staff = headcounts([item.id for item in page.items])
        for item in page.items:
            item.staff = staff.get(item.id, 0)
        return page

    def get(self, actor: Actor, department_id: uuid.UUID) -> Department:
        self.access.require(actor, "offices.read")
        department = self._require(actor, department_id)
        department.staff = headcount(department.id)
        return department

    def create(
        self,
        actor: Actor,
        *,
        name: str,
        office_id: uuid.UUID | None = None,
        code: str | None = None,
        description: str | None = None,
        head_employee_id: uuid.UUID | None = None,
        parent_department_id: uuid.UUID | None = None,
    ) -> Department:
        """Завести отдел.

        Офис необязателен, и обычно его не указывают: «Продажи» одни на
        всю компанию, и заводить их в каждом городе заново значило бы
        получить пять разных отделов продаж в одном отчёте.

        Если офис всё же назвали, это подразделение одного места. Тогда
        офис только выбирают: создавать и настраивать его отсюда нельзя —
        разводить два места, где правят адрес и геозону, значит получить
        два разных ответа на один вопрос.
        """
        self.access.require(actor, "departments.manage")
        office = (
            self.access.require_office(actor, office_id)
            if office_id is not None else None
        )

        parent = None
        if parent_department_id:
            parent = self._require(actor, parent_department_id)
            if office is not None and parent.office_id != office.id:
                # Иерархия внутри офиса. Отдел, вложенный в отдел другого
                # офиса, означал бы, что человек числится сразу в двух
                # местах, и «сотрудники офиса» перестало бы быть числом.
                raise ValidationFailed(
                    "Родительский отдел должен быть в том же офисе",
                    details={"parent_department_id": str(parent_department_id)},
                )

        head = self._head(actor, head_employee_id)

        with self.atomic():
            department = Department.objects.create(
                organization_id=actor.organization_id,
                office=office,
                parent_department=parent,
                code=(
                    clean_code(code, field="code") if code
                    else _free_code(
                        "DEP-",
                        set(
                            Department.objects.filter(
                                organization_id=actor.organization_id
                            ).values_list("code", flat=True)
                        ),
                    )
                ),
                name=clean_text(name, field="name", max_length=255),
                description=clean_text(
                    description, field="description", max_length=2000
                ),
                head_employee=head,
                status="ACTIVE",
            )
            self.audit.record(
                actor,
                action="department.create",
                entity_type="departments",
                entity_id=department.id,
                before=None,
                after=snapshot(department, DEPARTMENT_FIELDS),
            )
        return department

    def update(
        self,
        actor: Actor,
        department_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        head_employee_id: uuid.UUID | None = None,
        clear_head: bool = False,
    ) -> Department:
        """Название, описание и руководитель.

        Код — часть договорённости с внешними системами и отчётами; офис
        и родитель задают место отдела в структуре, и менять их правкой
        поля нельзя: это перевод людей, а не переименование.

        Снятие руководителя — отдельный признак, а не пустое значение:
        «поле не прислали» и «руководителя больше нет» иначе выглядели
        бы одинаково.
        """
        self.access.require(actor, "departments.manage")
        department = self._require(actor, department_id)
        before = snapshot(department, DEPARTMENT_FIELDS)

        if name is not None:
            department.name = clean_text(name, field="name", max_length=255)
        if description is not None:
            department.description = clean_text(
                description, field="description", max_length=2000
            )
        if clear_head:
            department.head_employee = None
        elif head_employee_id is not None:
            department.head_employee = self._head(actor, head_employee_id)

        with self.atomic():
            department.save()
            self.audit.record(
                actor,
                action="department.update",
                entity_type="departments",
                entity_id=department.id,
                before=before,
                after=snapshot(department, DEPARTMENT_FIELDS),
            )
        return department

    def set_status(
        self, actor: Actor, department_id: uuid.UUID, *, status: str
    ) -> Department:
        if status not in DEPARTMENT_STATUSES:
            raise ValidationFailed(
                "Неизвестный статус отдела",
                details={"status": status, "allowed": list(DEPARTMENT_STATUSES)},
            )
        self.access.require(actor, "departments.manage")
        department = self._require(actor, department_id)
        if department.status == status:
            return department

        # Отдел с людьми не архивируется. Иначе сотрудники остались бы
        # числиться там, куда в интерфейсе больше не попасть: их не
        # видно в структуре, а в отчётах они по-прежнему есть. Сначала
        # перевод людей, потом архив.
        if status == "INACTIVE":
            staff = headcount(department.id)
            if staff:
                raise Conflict(
                    f"В отделе ещё числятся сотрудники ({staff}). Переведите "
                    "их в другой отдел — после этого отдел можно архивировать",
                    details={"department_id": str(department.id), "staff": staff},
                )

        before = snapshot(department, DEPARTMENT_FIELDS)
        with self.atomic():
            department.status = status
            department.save(update_fields=["status", "updated_at"])
            self.audit.record(
                actor,
                action="department.status",
                entity_type="departments",
                entity_id=department.id,
                before=before,
                after=snapshot(department, DEPARTMENT_FIELDS),
            )
        return department

    def delete(self, actor: Actor, department_id: uuid.UUID) -> None:
        """Убрать отдел совсем — только пока на него никто не ссылался.

        Как только в отделе побывал хоть один человек, удаление
        запрещено: строка назначения отвечает на вопрос, где человек
        работал в прошлом году, и стереть отдел значит стереть ответ.
        Такой отдел архивируют.

        Разница между «удалить» и «архивировать» здесь настоящая, а не
        косметическая: кнопка «Удалить», которая на самом деле прячет,
        обманывает — человек считает, что убрал опечатку, а она осталась
        в отчётах.
        """
        self.access.require(actor, "departments.manage")
        department = self._require(actor, department_id)

        used = EmployeeAssignment.objects.filter(
            department_id=department.id
        ).count()
        if used:
            raise Conflict(
                f"Отдел уже встречается в назначениях сотрудников ({used}). "
                f"Его можно только архивировать: удаление стёрло бы ответ на "
                f"вопрос, где эти люди работали",
                details={"department_id": str(department.id), "used": used},
            )
        if Department.objects.filter(parent_department_id=department.id).exists():
            raise Conflict(
                "В отделе есть вложенные подразделения: сначала уберите их",
                details={"department_id": str(department.id)},
            )

        with self.atomic():
            self.audit.record(
                actor, action="department.delete",
                entity_type="departments", entity_id=department.id,
                before=snapshot(department, DEPARTMENT_FIELDS), after=None,
            )
            department.delete()

    def _head(self, actor: Actor, employee_id: uuid.UUID | None) -> Employee | None:
        """Руководитель — действующий сотрудник этой же организации."""
        if employee_id is None:
            return None
        employee = Employee.objects.filter(
            id=employee_id, organization_id=actor.organization_id
        ).first()
        if employee is None:
            raise ValidationFailed(
                "Такого сотрудника нет",
                details={"head_employee_id": str(employee_id)},
            )
        return employee

    def _require(self, actor: Actor, department_id: uuid.UUID) -> Department:
        department = (
            Department.objects.select_related("office", "head_employee")
            .filter(id=department_id, organization_id=actor.organization_id)
            .first()
        )
        if department is None:
            raise NotFound("Отдел не найден")
        # У общего отдела офиса нет, и проверять доступ к месту не к чему.
        if department.office_id is not None:
            self.access.require_office(actor, department.office_id)
        return department


class PositionService(BaseService):
    """Должности организации.

    Область видимости к ним не применяется: должность не принадлежит
    офису, и «Инженер» одинаков для всех. Организация при этом
    проверяется всегда.
    """

    def list(
        self,
        actor: Actor,
        *,
        search: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "employees.read")

        queryset = Position.objects.filter(organization_id=actor.organization_id)
        if status:
            queryset = queryset.filter(status=status)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(name__icontains=pattern) | queryset.filter(
                code__icontains=pattern
            )
        page = paginate(queryset, limit=limit, cursor=cursor)
        # Сколько человек занимают должность: по этому числу видно, что
        # архивирование «Инженера» затронет живых людей.
        used = position_usage([item.id for item in page.items])
        for item in page.items:
            item.staff = used.get(item.id, 0)
        return page

    def get(self, actor: Actor, position_id: uuid.UUID) -> Position:
        self.access.require(actor, "employees.read")
        return self._require(actor, position_id)

    def create(
        self,
        actor: Actor,
        *,
        name: str,
        code: str | None = None,
        description: str | None = None,
    ) -> Position:
        self.access.require(actor, "positions.manage")
        with self.atomic():
            position = Position.objects.create(
                organization_id=actor.organization_id,
                code=(
                    clean_code(code, field="code") if code
                    else _free_code(
                        "POS-",
                        set(
                            Position.objects.filter(
                                organization_id=actor.organization_id
                            ).values_list("code", flat=True)
                        ),
                    )
                ),
                name=clean_text(name, field="name", max_length=255),
                description=clean_text(description, field="description"),
                status="ACTIVE",
            )
            self.audit.record(
                actor,
                action="position.create",
                entity_type="positions",
                entity_id=position.id,
                before=None,
                after=snapshot(position, POSITION_FIELDS),
            )
        return position

    def update(
        self,
        actor: Actor,
        position_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Position:
        self.access.require(actor, "positions.manage")
        position = self._require(actor, position_id)
        before = snapshot(position, POSITION_FIELDS)

        if name is not None:
            position.name = clean_text(name, field="name", max_length=255)
        if description is not None:
            position.description = clean_text(description, field="description")

        with self.atomic():
            position.save()
            self.audit.record(
                actor,
                action="position.update",
                entity_type="positions",
                entity_id=position.id,
                before=before,
                after=snapshot(position, POSITION_FIELDS),
            )
        return position

    def set_status(
        self, actor: Actor, position_id: uuid.UUID, *, status: str
    ) -> Position:
        if status not in POSITION_STATUSES:
            raise ValidationFailed(
                "Неизвестный статус должности",
                details={"status": status, "allowed": list(POSITION_STATUSES)},
            )
        self.access.require(actor, "positions.manage")
        position = self._require(actor, position_id)
        if position.status == status:
            return position

        before = snapshot(position, POSITION_FIELDS)
        with self.atomic():
            position.status = status
            position.save(update_fields=["status", "updated_at"])
            self.audit.record(
                actor,
                action="position.status",
                entity_type="positions",
                entity_id=position.id,
                before=before,
                after=snapshot(position, POSITION_FIELDS),
            )
        return position

    def delete(self, actor: Actor, position_id: uuid.UUID) -> None:
        """Убрать должность совсем — только пока её никто не занимал."""
        self.access.require(actor, "positions.manage")
        position = self._require(actor, position_id)

        used = EmployeeAssignment.objects.filter(position_id=position.id).count()
        if used:
            raise Conflict(
                f"Должность уже встречается в назначениях сотрудников "
                f"({used}). Её можно только архивировать: удаление стёрло бы "
                f"ответ на вопрос, кем эти люди работали",
                details={"position_id": str(position.id), "used": used},
            )

        with self.atomic():
            self.audit.record(
                actor, action="position.delete",
                entity_type="positions", entity_id=position.id,
                before=snapshot(position, POSITION_FIELDS), after=None,
            )
            position.delete()

    def _require(self, actor: Actor, position_id: uuid.UUID) -> Position:
        position = Position.objects.filter(
            id=position_id, organization_id=actor.organization_id
        ).first()
        if position is None:
            # Чужая организация отвечает как отсутствие записи.
            raise NotFound("Должность не найдена")
        return position


__all__ = [
    "DepartmentService",
    "PositionService",
    "headcount",
    "headcounts",
    "position_usage",
]
