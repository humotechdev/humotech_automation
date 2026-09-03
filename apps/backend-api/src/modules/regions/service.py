"""Справочник регионов: чтение, изменение, включение и выключение.

Регион не удаляется: на него ссылаются офисы, а на офисы — назначения и
отметки. Внешние ключи объявлены с `ON DELETE RESTRICT`, поэтому попытка
физического удаления региона с историей отклоняется самой базой. Здесь
удаления нет вовсе — только перевод в `INACTIVE`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from src.core.errors import Conflict
from src.core.pagination import Page, paginate
from src.core.rbac import Actor, snapshot
from src.core.service import BaseService
from src.core.validation import clean_code, clean_text, validate_timezone
from src.modules.offices.models import Office
from src.modules.regions.models import Region
from src.modules.regions.schemas import (
    RegionCreateRequest,
    RegionListItem,
    RegionUpdateRequest,
    RegionView,
)

AUDITED_FIELDS = ("code", "name", "timezone", "status")


class RegionService(BaseService):
    """Требует `regions.read` для чтения и `regions.manage` для изменений."""

    # ------------------------------------------------------------------ чтение

    def list(
        self,
        actor: Actor,
        *,
        search: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "regions.read")

        statement = select(Region).where(
            Region.organization_id == actor.organization_id
        )
        visible = self.access.region_filter(actor)
        if visible is not None:
            statement = statement.where(visible)
        if status:
            statement = statement.where(Region.status == status)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                Region.name.ilike(pattern) | Region.code.ilike(pattern)
            )

        page = paginate(self.session, statement, Region, limit=limit, cursor=cursor)

        # Число офисов — одним запросом на всю страницу, а не по одному
        # на регион: иначе список из пятидесяти регионов даёт полсотни запросов.
        counts: dict[uuid.UUID, int] = {}
        if page.items:
            rows = self.session.execute(
                select(Office.region_id, func.count(Office.id))
                .where(Office.region_id.in_([r.id for r in page.items]))
                .group_by(Office.region_id)
            ).all()
            counts = {region_id: count for region_id, count in rows}

        return Page(
            items=[
                RegionListItem(
                    **RegionView.model_validate(region).model_dump(),
                    offices_count=counts.get(region.id, 0),
                )
                for region in page.items
            ],
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def get(self, actor: Actor, region_id: uuid.UUID) -> RegionView:
        self.access.require(actor, "regions.read")
        region = self.access.require_region(actor, region_id)
        return RegionView.model_validate(region)

    # --------------------------------------------------------------- изменение

    def create(self, actor: Actor, request: RegionCreateRequest) -> RegionView:
        self.access.require(actor, "regions.manage")

        region = Region(
            organization_id=actor.organization_id,
            code=clean_code(request.code),
            name=clean_text(request.name, field="name", required=True),
            timezone=validate_timezone(request.timezone, required=False),
            status="ACTIVE",
        )
        with self.atomic():
            self.session.add(region)
            self.flush()
            self.audit.record(
                actor, action="region.create", entity_type="regions",
                entity_id=region.id, after=snapshot(region, AUDITED_FIELDS),
            )
        return RegionView.model_validate(region)

    def update(
        self, actor: Actor, region_id: uuid.UUID, request: RegionUpdateRequest
    ) -> RegionView:
        self.access.require(actor, "regions.manage")
        region = self.access.require_region(actor, region_id)

        before = snapshot(region, AUDITED_FIELDS)
        changed = request.model_fields_set
        if "code" in changed and request.code is not None:
            region.code = clean_code(request.code)
        if "name" in changed and request.name is not None:
            region.name = clean_text(request.name, field="name", required=True)
        if "timezone" in changed:
            # передан явный null — регион снова наследует пояс организации
            region.timezone = validate_timezone(request.timezone, required=False)

        with self.atomic():
            self.flush()
            self.audit.record(
                actor, action="region.update", entity_type="regions",
                entity_id=region.id, before=before,
                after=snapshot(region, AUDITED_FIELDS),
            )
        return RegionView.model_validate(region)

    def deactivate(self, actor: Actor, region_id: uuid.UUID) -> RegionView:
        """Регион выключается, офисы и история сотрудников остаются на месте."""
        return self._set_status(actor, region_id, "INACTIVE", "region.deactivate")

    def reactivate(self, actor: Actor, region_id: uuid.UUID) -> RegionView:
        return self._set_status(actor, region_id, "ACTIVE", "region.reactivate")

    def _set_status(
        self, actor: Actor, region_id: uuid.UUID, status: str, action: str
    ) -> RegionView:
        self.access.require(actor, "regions.manage")
        region = self.access.require_region(actor, region_id)
        if region.status == status:
            raise Conflict(
                f"Регион уже в статусе {status}", details={"status": status}
            )
        if region.status == "ARCHIVED":
            raise Conflict("Регион в архиве, изменение статуса недоступно")

        before = snapshot(region, AUDITED_FIELDS)
        region.status = status
        with self.atomic():
            self.flush()
            self.audit.record(
                actor, action=action, entity_type="regions", entity_id=region.id,
                before=before, after=snapshot(region, AUDITED_FIELDS),
            )
        return RegionView.model_validate(region)
