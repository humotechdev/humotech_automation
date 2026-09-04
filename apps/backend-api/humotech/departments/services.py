"""Справочники отделов и должностей.

Оба — плоские справочники без собственной логики, поэтому лежат в одном
модуле: заводить два почти одинаковых файла ради симметрии каталогов
незачем.

Отдел привязан к офису, должность — к организации целиком. Это не
случайность схемы: «Отдел разработки» в Душанбе и в Худжанде — разные
отделы с разными людьми, а должность «Инженер» одна на всю компанию,
иначе её пришлось бы заводить в каждом офисе заново.

Ни то ни другое не удаляется. На отдел и должность ссылаются назначения
сотрудников, в том числе закрытые: удаление стёрло бы ответ на вопрос
«кем человек работал в прошлом году». Есть только перевод в `INACTIVE`.
"""

from __future__ import annotations

import uuid

from humotech.core.enums import DEPARTMENT_STATUSES, POSITION_STATUSES
from humotech.core.errors import NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_code, clean_text
from humotech.departments.models import Department
from humotech.positions.models import Position

DEPARTMENT_FIELDS = ("code", "name", "status")
POSITION_FIELDS = ("code", "name", "description", "status")


class DepartmentService(BaseService):
    """Отделы офиса.

    Чтение — по `offices.read`: отдел виден тому, кому виден его офис,
    отдельного разрешения на просмотр справочника в каталоге нет.
    Изменение — по `departments.manage`.
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
            queryset = queryset.filter(office_id=office_id)
        elif region_id:
            self.access.require_region(actor, region_id)
            queryset = queryset.filter(office__region_id=region_id)
        else:
            visible = self.access.visible_office_ids(actor)
            if visible is not None:
                queryset = queryset.filter(office_id__in=visible)

        if status:
            queryset = queryset.filter(status=status)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(name__icontains=pattern) | queryset.filter(
                code__icontains=pattern
            )
        return paginate(queryset, limit=limit, cursor=cursor)

    def get(self, actor: Actor, department_id: uuid.UUID) -> Department:
        self.access.require(actor, "offices.read")
        return self._require(actor, department_id)

    def create(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID,
        code: str,
        name: str,
        parent_department_id: uuid.UUID | None = None,
    ) -> Department:
        self.access.require(actor, "departments.manage")
        office = self.access.require_office(actor, office_id)

        parent = None
        if parent_department_id:
            parent = self._require(actor, parent_department_id)
            if parent.office_id != office.id:
                # Иерархия внутри офиса. Отдел, вложенный в отдел другого
                # офиса, означал бы, что человек числится сразу в двух
                # местах, и «сотрудники офиса» перестало бы быть числом.
                raise ValidationFailed(
                    "Родительский отдел должен быть в том же офисе",
                    details={"parent_department_id": str(parent_department_id)},
                )

        with self.atomic():
            department = Department.objects.create(
                organization_id=actor.organization_id,
                office=office,
                parent_department=parent,
                code=clean_code(code, field="code"),
                name=clean_text(name, field="name", max_length=255),
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
    ) -> Department:
        """Меняется только название.

        Код — часть договорённости с внешними системами и отчётами; офис
        и родитель задают место отдела в структуре, и менять их правкой
        поля нельзя: это перевод людей, а не переименование.
        """
        self.access.require(actor, "departments.manage")
        department = self._require(actor, department_id)
        before = snapshot(department, DEPARTMENT_FIELDS)

        if name is not None:
            department.name = clean_text(name, field="name", max_length=255)

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

    def _require(self, actor: Actor, department_id: uuid.UUID) -> Department:
        department = (
            Department.objects.select_related("office")
            .filter(id=department_id, organization_id=actor.organization_id)
            .first()
        )
        if department is None:
            raise NotFound("Отдел не найден")
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
        return paginate(queryset, limit=limit, cursor=cursor)

    def get(self, actor: Actor, position_id: uuid.UUID) -> Position:
        self.access.require(actor, "employees.read")
        return self._require(actor, position_id)

    def create(
        self,
        actor: Actor,
        *,
        code: str,
        name: str,
        description: str | None = None,
    ) -> Position:
        self.access.require(actor, "positions.manage")
        with self.atomic():
            position = Position.objects.create(
                organization_id=actor.organization_id,
                code=clean_code(code, field="code"),
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

    def _require(self, actor: Actor, position_id: uuid.UUID) -> Position:
        position = Position.objects.filter(
            id=position_id, organization_id=actor.organization_id
        ).first()
        if position is None:
            # Чужая организация отвечает как отсутствие записи.
            raise NotFound("Должность не найдена")
        return position


__all__ = ["DepartmentService", "PositionService"]
