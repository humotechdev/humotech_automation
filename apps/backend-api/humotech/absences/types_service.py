"""Справочник причин отсутствия: отпуск, больничный, отгул и прочие.

Отдельно от `services.py`: там заявки сотрудника — кто, когда и на
сколько отсутствует, — а здесь настройка самих видов. Разная аудитория
и разные права: заявки подаёт человек, справочник ведёт кадровик по
`absences.manage_types`.

Удаления здесь нет и не будет. На вид отсутствия ссылаются заявки и
подтверждённые периоды прошлых лет: стереть «Больничный» значит
потерять ответ на вопрос, почему человека не было в марте. Вид
выключают — он перестаёт предлагаться в новых заявках и остаётся в
истории.
"""

from __future__ import annotations

import uuid

from django.db.models import Count

from humotech.absences.models import AbsenceRequest, AbsenceType, EmployeeAbsence
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_code, clean_text

AUDITED = (
    "code", "name", "is_paid", "requires_approval", "requires_document",
    "document_required_after_days", "deducts_leave_balance", "is_active",
)


def _free_code(organization_id: uuid.UUID) -> str:
    """Код нового вида придумывает сервер.

    По коду стоит уникальный ключ и на него ссылаются выгрузки, но
    кадровику он не нужен: в интерфейсе вид опознаётся названием. Просить
    человека придумать `SICK_LEAVE_2` — значит просить его придумать
    техническую подробность.
    """
    taken = set(
        AbsenceType.objects.filter(organization_id=organization_id).values_list(
            "code", flat=True
        )
    )
    number = len(taken) + 1
    while f"ABS-{number}" in taken:
        number += 1
    return f"ABS-{number}"


def usage(type_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Сколько раз вид уже встречается в истории.

    Считаются и заявки, и подтверждённые периоды: и то и другое делает
    вид неудаляемым, и кадровику важно видеть число до того, как он
    попробует его убрать.
    """
    counts: dict[uuid.UUID, int] = {one: 0 for one in type_ids}
    for model in (AbsenceRequest, EmployeeAbsence):
        rows = (
            model.objects.filter(absence_type_id__in=type_ids)
            .values("absence_type_id")
            .annotate(n=Count("id"))
        )
        for row in rows:
            key = row["absence_type_id"]
            counts[key] = counts.get(key, 0) + row["n"]
    return counts


class AbsenceTypeService(BaseService):
    """Ведение справочника видов отсутствия."""

    def list(
        self, actor: Actor, *, search: str | None = None, status: str | None = None,
        limit: int | None = None, cursor: str | None = None,
    ) -> Page:
        # Читать справочник может тот, кто вообще видит отсутствия:
        # отдельного разрешения на просмотр списка видов в каталоге нет.
        self.access.require(actor, "absences.read")

        queryset = AbsenceType.objects.filter(
            organization_id=actor.organization_id
        ).order_by("name")
        if status == "ACTIVE":
            queryset = queryset.filter(is_active=True)
        elif status == "INACTIVE":
            queryset = queryset.filter(is_active=False)
        if search:
            queryset = queryset.filter(name__icontains=search.strip())

        page = paginate(queryset, limit=limit, cursor=cursor)
        used = usage([item.id for item in page.items])
        for item in page.items:
            item.used = used.get(item.id, 0)
        return page

    def get(self, actor: Actor, type_id: uuid.UUID) -> AbsenceType:
        self.access.require(actor, "absences.read")
        item = self._require(actor, type_id)
        item.used = usage([item.id]).get(item.id, 0)
        return item

    def create(
        self, actor: Actor, *, name: str, is_paid: bool = False,
        requires_approval: bool = True, requires_document: bool = False,
        document_required_after_days: int | None = None,
        deducts_leave_balance: bool = False, code: str | None = None,
    ) -> AbsenceType:
        self.access.require(actor, "absences.manage_types")
        self._check_days(requires_document, document_required_after_days)

        with self.atomic():
            item = AbsenceType.objects.create(
                organization_id=actor.organization_id,
                code=(
                    clean_code(code, field="code") if code
                    else _free_code(actor.organization_id)
                ),
                name=clean_text(name, field="name", required=True, max_length=255),
                is_paid=is_paid,
                requires_approval=requires_approval,
                requires_document=requires_document,
                document_required_after_days=document_required_after_days,
                deducts_leave_balance=deducts_leave_balance,
                is_active=True,
            )
            self.audit.record(
                actor, action="absence_type.create", entity_type="absence_types",
                entity_id=item.id, before=None, after=snapshot(item, AUDITED),
            )
        item.used = 0
        return item

    def update(
        self, actor: Actor, type_id: uuid.UUID, *, name: str | None = None,
        is_paid: bool | None = None, requires_approval: bool | None = None,
        requires_document: bool | None = None,
        document_required_after_days: int | None = None,
        deducts_leave_balance: bool | None = None,
    ) -> AbsenceType:
        self.access.require(actor, "absences.manage_types")
        item = self._require(actor, type_id)
        before = snapshot(item, AUDITED)

        if name is not None:
            item.name = clean_text(name, field="name", required=True, max_length=255)
        if is_paid is not None:
            item.is_paid = is_paid
        if requires_approval is not None:
            item.requires_approval = requires_approval
        if requires_document is not None:
            item.requires_document = requires_document
        if document_required_after_days is not None:
            item.document_required_after_days = document_required_after_days
        if deducts_leave_balance is not None:
            item.deducts_leave_balance = deducts_leave_balance
        self._check_days(item.requires_document, item.document_required_after_days)

        with self.atomic():
            item.save()
            self.audit.record(
                actor, action="absence_type.update", entity_type="absence_types",
                entity_id=item.id, before=before, after=snapshot(item, AUDITED),
            )
        item.used = usage([item.id]).get(item.id, 0)
        return item

    def set_active(
        self, actor: Actor, type_id: uuid.UUID, *, active: bool
    ) -> AbsenceType:
        """Включить или выключить вид.

        Выключенный не предлагается в новых заявках, но остаётся в
        истории и в отчётах. Это и есть «архивировать»: удаления у вида
        отсутствия нет ни в одном виде.
        """
        self.access.require(actor, "absences.manage_types")
        item = self._require(actor, type_id)
        if item.is_active == active:
            return item

        before = snapshot(item, AUDITED)
        with self.atomic():
            item.is_active = active
            item.save(update_fields=["is_active", "updated_at"])
            self.audit.record(
                actor, action="absence_type.status", entity_type="absence_types",
                entity_id=item.id, before=before, after=snapshot(item, AUDITED),
            )
        item.used = usage([item.id]).get(item.id, 0)
        return item

    def delete(self, actor: Actor, type_id: uuid.UUID) -> None:
        """Убрать вид совсем — только пока он не встречался в истории.

        Как только по нему подали заявку, удаление запрещено: иначе
        пропал бы ответ на вопрос, почему человека не было в марте.
        Такой вид выключают.
        """
        self.access.require(actor, "absences.manage_types")
        item = self._require(actor, type_id)

        used = usage([item.id]).get(item.id, 0)
        if used:
            raise Conflict(
                f"Причина уже встречается в заявках и периодах ({used}). "
                f"Её можно только отключить: удаление стёрло бы ответ на "
                f"вопрос, почему людей не было",
                details={"absence_type_id": str(item.id), "used": used},
            )

        with self.atomic():
            self.audit.record(
                actor, action="absence_type.delete",
                entity_type="absence_types", entity_id=item.id,
                before=snapshot(item, AUDITED), after=None,
            )
            item.delete()

    # --- внутреннее ---------------------------------------------------------

    @staticmethod
    def _check_days(requires_document: bool, days: int | None) -> None:
        if days is not None and days < 0:
            raise ValidationFailed(
                "Число дней не может быть отрицательным",
                details={"document_required_after_days": days},
            )
        if days is not None and not requires_document:
            raise ValidationFailed(
                "Срок предоставления документа имеет смысл только там, где "
                "документ нужен",
                details={"requires_document": False},
            )

    def _require(self, actor: Actor, type_id: uuid.UUID) -> AbsenceType:
        item = AbsenceType.objects.filter(
            id=type_id, organization_id=actor.organization_id
        ).first()
        if item is None:
            # Чужая организация отвечает как отсутствие записи.
            raise NotFound("Вид отсутствия не найден")
        return item


__all__ = ["AbsenceTypeService", "usage"]
