"""Справочник регионов: чтение, изменение, включение и выключение.

Регион не удаляется: на него ссылаются офисы, а на офисы — назначения и
отметки. Внешние ключи объявлены с `ON DELETE RESTRICT`, поэтому попытка
физического удаления региона с историей отклоняется самой базой. Здесь
удаления нет вовсе — только перевод в `INACTIVE`.

Бизнес-логика живёт здесь, а не во view и не в сериализаторе: правила нужны
и REST API, и будущему Telegram-боту, и командам обслуживания.
"""

from __future__ import annotations

import uuid

from django.db.models import Count

from humotech.core.errors import Conflict
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_code, clean_text, validate_timezone
from humotech.regions.models import Region

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

        queryset = Region.objects.filter(organization_id=actor.organization_id)
        visible = self.access.region_filter(actor)
        if visible is not None:
            queryset = queryset.filter(visible)
        if status:
            queryset = queryset.filter(status=status)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(name__icontains=pattern) | queryset.filter(
                code__icontains=pattern
            )

        # Число офисов считается тем же запросом, а не отдельным на каждый
        # регион: список из пятидесяти регионов иначе даёт полсотни запросов.
        queryset = queryset.annotate(offices_count=Count("offices"))
        return paginate(queryset, limit=limit, cursor=cursor)

    def get(self, actor: Actor, region_id: uuid.UUID) -> Region:
        self.access.require(actor, "regions.read")
        return self.access.require_region(actor, region_id)

    # --------------------------------------------------------------- изменение

    def create(
        self, actor: Actor, *, code: str, name: str, timezone: str | None = None
    ) -> Region:
        self.access.require(actor, "regions.manage")

        with self.atomic():
            region = Region.objects.create(
                organization_id=actor.organization_id,
                code=clean_code(code),
                name=clean_text(name, field="name", required=True),
                timezone=validate_timezone(timezone, required=False),
                status="ACTIVE",
            )
            self.audit.record(
                actor, action="region.create", entity_type="regions",
                entity_id=region.id, after=snapshot(region, AUDITED_FIELDS),
            )
        return region

    def update(self, actor: Actor, region_id: uuid.UUID, **changes) -> Region:
        """Меняются только переданные поля.

        Ключ `timezone` со значением `None` означает «наследовать пояс
        организации», поэтому отсутствие ключа и явный `None` — разные вещи.
        """
        self.access.require(actor, "regions.manage")
        region = self.access.require_region(actor, region_id)

        before = snapshot(region, AUDITED_FIELDS)
        if "code" in changes and changes["code"] is not None:
            region.code = clean_code(changes["code"])
        if "name" in changes and changes["name"] is not None:
            region.name = clean_text(changes["name"], field="name", required=True)
        if "timezone" in changes:
            region.timezone = validate_timezone(changes["timezone"], required=False)

        with self.atomic():
            region.save()
            self.audit.record(
                actor, action="region.update", entity_type="regions",
                entity_id=region.id, before=before,
                after=snapshot(region, AUDITED_FIELDS),
            )
        return region

    def deactivate(self, actor: Actor, region_id: uuid.UUID) -> Region:
        """Регион выключается, офисы и история сотрудников остаются на месте."""
        return self._set_status(actor, region_id, "INACTIVE", "region.deactivate")

    def reactivate(self, actor: Actor, region_id: uuid.UUID) -> Region:
        return self._set_status(actor, region_id, "ACTIVE", "region.reactivate")

    def _set_status(
        self, actor: Actor, region_id: uuid.UUID, status: str, action: str
    ) -> Region:
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
            region.save()
            self.audit.record(
                actor, action=action, entity_type="regions", entity_id=region.id,
                before=before, after=snapshot(region, AUDITED_FIELDS),
            )
        return region
