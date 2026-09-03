"""Справочник офисов.

Офис — узел, к которому привязано почти всё остальное: отделы, QR-точки,
назначения сотрудников, отметки о входе. Поэтому удаления здесь нет: офис
переводится в `INACTIVE` (временно не работает) или в `CLOSED` (закрыт
насовсем). История отметок и назначений при этом не трогается.

Разница между `INACTIVE` и `CLOSED` смысловая, и она уже заложена в схеме:
первый статус обратим, второй — нет. Новых значений статуса не вводим.
"""

from __future__ import annotations

import uuid
from datetime import date

from humotech.core.errors import Conflict
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import (
    clean_code,
    clean_text,
    require_order,
    validate_timezone,
)
from humotech.offices.models import Office

AUDITED_FIELDS = (
    "code", "name", "address", "timezone", "status", "region_id",
    "opened_at", "closed_at",
)

SIMPLE_FIELDS = ("latitude", "longitude", "geofence_radius_m", "opened_at",
                 "closed_at")


class OfficeService(BaseService):
    """Требует `offices.read` для чтения и `offices.manage` для изменений."""

    # ------------------------------------------------------------------ чтение

    def list(
        self,
        actor: Actor,
        *,
        search: str | None = None,
        status: str | None = None,
        region_id: uuid.UUID | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "offices.read")

        queryset = Office.objects.filter(organization_id=actor.organization_id)
        visible = self.access.office_filter(actor)
        if visible is not None:
            queryset = queryset.filter(visible)
        if region_id is not None:
            # регион чужой организации не должен давать ни строк, ни подсказок
            self.access.require_region(actor, region_id)
            queryset = queryset.filter(region_id=region_id)
        if status:
            queryset = queryset.filter(status=status)
        if search:
            pattern = search.strip()
            queryset = (
                queryset.filter(name__icontains=pattern)
                | queryset.filter(code__icontains=pattern)
                | queryset.filter(address__icontains=pattern)
            )

        # Регион подтягивается тем же запросом: иначе обращение к `office.region`
        # в сериализаторе даёт по запросу на строку.
        return paginate(queryset.select_related("region"), limit=limit, cursor=cursor)

    def get(self, actor: Actor, office_id: uuid.UUID) -> Office:
        self.access.require(actor, "offices.read")
        return self.access.require_office(actor, office_id)

    # --------------------------------------------------------------- изменение

    def create(
        self,
        actor: Actor,
        *,
        region_id: uuid.UUID,
        code: str,
        name: str,
        address: str,
        timezone: str,
        **extra,
    ) -> Office:
        self.access.require(actor, "offices.manage")
        region = self.access.require_region(actor, region_id)
        if region.status != "ACTIVE":
            raise Conflict(
                "Регион не активен: новый офис в него добавить нельзя",
                details={"region_id": str(region.id), "status": region.status},
            )

        with self.atomic():
            office = Office.objects.create(
                organization_id=actor.organization_id,
                region=region,
                code=clean_code(code),
                name=clean_text(name, field="name", required=True),
                address=clean_text(address, field="address", required=True),
                timezone=validate_timezone(timezone),
                status="ACTIVE",
                **{k: extra.get(k) for k in SIMPLE_FIELDS if k in extra},
            )
            self.audit.record(
                actor, action="office.create", entity_type="offices",
                entity_id=office.id, after=snapshot(office, AUDITED_FIELDS),
            )
        return office

    def update(self, actor: Actor, office_id: uuid.UUID, **changes) -> Office:
        self.access.require(actor, "offices.manage")
        office = self.access.require_office(actor, office_id)

        before = snapshot(office, AUDITED_FIELDS)

        if changes.get("region_id") is not None:
            region = self.access.require_region(actor, changes["region_id"])
            if region.status != "ACTIVE" and region.id != office.region_id:
                raise Conflict(
                    "Регион не активен: переносить в него офис нельзя",
                    details={"region_id": str(region.id), "status": region.status},
                )
            office.region = region
        if changes.get("code") is not None:
            office.code = clean_code(changes["code"])
        if changes.get("name") is not None:
            office.name = clean_text(changes["name"], field="name", required=True)
        if changes.get("address") is not None:
            office.address = clean_text(
                changes["address"], field="address", required=True
            )
        if changes.get("timezone") is not None:
            office.timezone = validate_timezone(changes["timezone"])
        for field in SIMPLE_FIELDS:
            if field in changes:
                setattr(office, field, changes[field])

        require_order(
            office.opened_at, office.closed_at,
            message="Дата закрытия офиса раньше даты открытия",
            details={"opened_at": str(office.opened_at),
                     "closed_at": str(office.closed_at)},
        )

        with self.atomic():
            office.save()
            self.audit.record(
                actor, action="office.update", entity_type="offices",
                entity_id=office.id, before=before,
                after=snapshot(office, AUDITED_FIELDS),
            )
        return office

    def deactivate(self, actor: Actor, office_id: uuid.UUID) -> Office:
        """Временное выключение. Назначения и отметки сохраняются."""
        return self._set_status(actor, office_id, "INACTIVE", "office.deactivate")

    def reactivate(self, actor: Actor, office_id: uuid.UUID) -> Office:
        return self._set_status(actor, office_id, "ACTIVE", "office.reactivate")

    def close(
        self, actor: Actor, office_id: uuid.UUID, *, closed_at: date | None = None
    ) -> Office:
        """Закрытие насовсем. Обратной операции нет — это отдельный статус."""
        return self._set_status(
            actor, office_id, "CLOSED", "office.close", closed_at=closed_at
        )

    def _set_status(
        self, actor: Actor, office_id: uuid.UUID, status: str, action: str,
        *, closed_at: date | None = None,
    ) -> Office:
        self.access.require(actor, "offices.manage")
        office = self.access.require_office(actor, office_id)

        if office.status == status:
            raise Conflict(f"Офис уже в статусе {status}",
                           details={"status": status})
        if office.status in ("CLOSED", "ARCHIVED"):
            raise Conflict(
                "Офис закрыт: изменить его статус нельзя",
                details={"status": office.status},
            )

        before = snapshot(office, AUDITED_FIELDS)
        office.status = status
        if status == "CLOSED":
            office.closed_at = closed_at or date.today()
            require_order(
                office.opened_at, office.closed_at,
                message="Дата закрытия офиса раньше даты открытия",
            )

        with self.atomic():
            office.save()
            self.audit.record(
                actor, action=action, entity_type="offices", entity_id=office.id,
                before=before, after=snapshot(office, AUDITED_FIELDS),
            )
        return office

    # ------------------------------------------------------ вспомогательное

    def require_assignable(self, actor: Actor, office_id: uuid.UUID) -> Office:
        """Офис, в который допустимо назначать сотрудника.

        Отдельный метод, потому что правило одно и то же для создания
        сотрудника, перевода и выдачи доступа — а разойтись оно не должно.
        """
        office = self.access.require_office(actor, office_id)
        if office.status != "ACTIVE":
            raise Conflict(
                "Офис не активен: назначать в него сотрудников нельзя",
                details={"office_id": str(office_id), "status": office.status},
            )
        return office
