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

from sqlalchemy import select

from src.core.errors import Conflict
from src.core.pagination import Page, paginate
from src.core.rbac import Actor, snapshot
from src.core.service import BaseService
from src.core.validation import (
    clean_code,
    clean_text,
    require_order,
    validate_timezone,
)
from src.modules.offices.models import Office
from src.modules.offices.schemas import (
    OfficeCreateRequest,
    OfficeListItem,
    OfficeUpdateRequest,
    OfficeView,
)
from src.modules.regions.models import Region

AUDITED_FIELDS = (
    "code", "name", "address", "timezone", "status", "region_id",
    "opened_at", "closed_at",
)


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

        statement = select(Office).where(
            Office.organization_id == actor.organization_id
        )
        visible = self.access.office_filter(actor)
        if visible is not None:
            statement = statement.where(visible)
        if region_id is not None:
            # регион чужой организации не должен давать ни строк, ни подсказок
            self.access.require_region(actor, region_id)
            statement = statement.where(Office.region_id == region_id)
        if status:
            statement = statement.where(Office.status == status)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                Office.name.ilike(pattern)
                | Office.code.ilike(pattern)
                | Office.address.ilike(pattern)
            )

        page = paginate(self.session, statement, Office, limit=limit, cursor=cursor)

        # Регионы страницы — одним запросом.
        regions: dict[uuid.UUID, Region] = {}
        if page.items:
            regions = {
                region.id: region
                for region in self.session.scalars(
                    select(Region).where(
                        Region.id.in_({o.region_id for o in page.items})
                    )
                )
            }

        return Page(
            items=[
                OfficeListItem(
                    **OfficeView.model_validate(office).model_dump(),
                    region_code=getattr(regions.get(office.region_id), "code", None),
                    region_name=getattr(regions.get(office.region_id), "name", None),
                )
                for office in page.items
            ],
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def get(self, actor: Actor, office_id: uuid.UUID) -> OfficeView:
        self.access.require(actor, "offices.read")
        office = self.access.require_office(actor, office_id)
        return OfficeView.model_validate(office)

    # --------------------------------------------------------------- изменение

    def create(self, actor: Actor, request: OfficeCreateRequest) -> OfficeView:
        self.access.require(actor, "offices.manage")
        region = self.access.require_region(actor, request.region_id)
        if region.status != "ACTIVE":
            raise Conflict(
                "Регион не активен: новый офис в него добавить нельзя",
                details={"region_id": str(region.id), "status": region.status},
            )

        office = Office(
            organization_id=actor.organization_id,
            region_id=region.id,
            code=clean_code(request.code),
            name=clean_text(request.name, field="name", required=True),
            address=clean_text(request.address, field="address", required=True),
            timezone=validate_timezone(request.timezone),
            latitude=request.latitude,
            longitude=request.longitude,
            geofence_radius_m=request.geofence_radius_m,
            status="ACTIVE",
            opened_at=request.opened_at,
        )
        with self.atomic():
            self.session.add(office)
            self.flush()
            self.audit.record(
                actor, action="office.create", entity_type="offices",
                entity_id=office.id, after=snapshot(office, AUDITED_FIELDS),
            )
        return OfficeView.model_validate(office)

    def update(
        self, actor: Actor, office_id: uuid.UUID, request: OfficeUpdateRequest
    ) -> OfficeView:
        self.access.require(actor, "offices.manage")
        office = self.access.require_office(actor, office_id)

        before = snapshot(office, AUDITED_FIELDS)
        changed = request.model_fields_set

        if "region_id" in changed and request.region_id is not None:
            region = self.access.require_region(actor, request.region_id)
            if region.status != "ACTIVE" and region.id != office.region_id:
                raise Conflict(
                    "Регион не активен: переносить в него офис нельзя",
                    details={"region_id": str(region.id), "status": region.status},
                )
            office.region_id = region.id
        if "code" in changed and request.code is not None:
            office.code = clean_code(request.code)
        if "name" in changed and request.name is not None:
            office.name = clean_text(request.name, field="name", required=True)
        if "address" in changed and request.address is not None:
            office.address = clean_text(
                request.address, field="address", required=True
            )
        if "timezone" in changed and request.timezone is not None:
            office.timezone = validate_timezone(request.timezone)
        for field in ("latitude", "longitude", "geofence_radius_m", "opened_at",
                      "closed_at"):
            if field in changed:
                setattr(office, field, getattr(request, field))

        require_order(
            office.opened_at, office.closed_at,
            message="Дата закрытия офиса раньше даты открытия",
            details={"opened_at": str(office.opened_at),
                     "closed_at": str(office.closed_at)},
        )

        with self.atomic():
            self.flush()
            self.audit.record(
                actor, action="office.update", entity_type="offices",
                entity_id=office.id, before=before,
                after=snapshot(office, AUDITED_FIELDS),
            )
        return OfficeView.model_validate(office)

    def deactivate(self, actor: Actor, office_id: uuid.UUID) -> OfficeView:
        """Временное выключение. Назначения и отметки сохраняются."""
        return self._set_status(actor, office_id, "INACTIVE", "office.deactivate")

    def reactivate(self, actor: Actor, office_id: uuid.UUID) -> OfficeView:
        return self._set_status(actor, office_id, "ACTIVE", "office.reactivate")

    def close(
        self, actor: Actor, office_id: uuid.UUID, *, closed_at: date | None = None
    ) -> OfficeView:
        """Закрытие насовсем. Обратной операции нет — это отдельный статус."""
        return self._set_status(
            actor, office_id, "CLOSED", "office.close", closed_at=closed_at
        )

    def _set_status(
        self, actor: Actor, office_id: uuid.UUID, status: str, action: str,
        *, closed_at: date | None = None,
    ) -> OfficeView:
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
            self.flush()
            self.audit.record(
                actor, action=action, entity_type="offices", entity_id=office.id,
                before=before, after=snapshot(office, AUDITED_FIELDS),
            )
        return OfficeView.model_validate(office)

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
