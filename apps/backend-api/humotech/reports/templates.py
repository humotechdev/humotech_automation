"""Шаблоны конструктора отчётов: сохранить, открыть, удалить.

Шаблон личный и хранит ровно то, что человек настроил: вид, период или
правило периода («этот месяц»), офисы, отделы, сотрудника, поля, формат
и имя файла. Права при этом не сохраняются: шаблон с офисом, который
у человека потом отобрали, соберётся только по его новой области.
"""

from __future__ import annotations

import uuid

from humotech.core.errors import NotFound, ValidationFailed
from humotech.core.rbac import Actor
from humotech.core.service import BaseService
from humotech.reports.builder import ReportBuilderService, ReportSpec

TEMPLATE_NAME_MAX = 120
TEMPLATE_FIELDS = ("name", "kind", "fmt")


class ReportTemplateService(BaseService):
    def list(self, actor: Actor):
        from humotech.reports.models import ReportTemplate

        self.access.require(actor, "reports.export")
        return list(
            ReportTemplate.objects.filter(
                organization_id=actor.organization_id, owner_user_id=actor.user_id
            ).order_by("name")
        )

    def save(self, actor: Actor, *, name: str, fmt: str, spec: ReportSpec):
        """Сохранить. То же имя — перезаписать: второй «Посещаемость офиса» не нужен."""
        from humotech.reports.models import ReportTemplate

        # Права на вид и область — как при заказе: шаблон, по которому
        # нельзя собрать отчёт, сохранять незачем.
        ReportBuilderService().check(actor, spec)
        title = " ".join(str(name or "").split())[:TEMPLATE_NAME_MAX]
        if not title:
            raise ValidationFailed(
                "Назовите шаблон",
                details={"template_name": ["Введите название шаблона"]},
            )

        with self.atomic():
            template = (
                ReportTemplate.objects.select_for_update()
                .filter(owner_user_id=actor.user_id, name=title)
                .first()
            )
            before = None
            if template is None:
                template = ReportTemplate(
                    organization_id=actor.organization_id,
                    owner_user_id=actor.user_id,
                    name=title,
                )
                action = "report.template.create"
            else:
                before = {"name": template.name, "kind": template.kind,
                          "fmt": template.fmt, "filters": template.filters}
                action = "report.template.update"
            template.kind = spec.kind
            template.fmt = fmt
            template.filters = spec.to_filters()
            template.save()
            self.audit.record(
                actor,
                action=action,
                entity_type="report_templates",
                entity_id=template.id,
                before=before,
                after={"name": template.name, "kind": template.kind,
                       "fmt": template.fmt, "filters": template.filters},
            )
        return template, before is None

    def delete(self, actor: Actor, template_id: uuid.UUID) -> None:
        from humotech.reports.models import ReportTemplate

        self.access.require(actor, "reports.export")
        template = ReportTemplate.objects.filter(
            id=template_id,
            organization_id=actor.organization_id,
            owner_user_id=actor.user_id,
        ).first()
        if template is None:
            raise NotFound("Шаблон не найден")
        with self.atomic():
            self.audit.record(
                actor,
                action="report.template.delete",
                entity_type="report_templates",
                entity_id=template.id,
                before={"name": template.name, "kind": template.kind,
                        "fmt": template.fmt},
            )
            template.delete()


__all__ = ["ReportTemplateService"]
