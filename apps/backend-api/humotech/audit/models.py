"""Аудит изменений. Таблица неизменяемая: только INSERT, никаких UPDATE/DELETE.

`entity_id` — полиморфная ссылка на любую сущность системы, поэтому обычного
внешнего ключа у неё нет и быть не может. Ссылочную целостность здесь заменяет
пара (entity_type, entity_id).
"""

from __future__ import annotations

from django.db import models

from humotech.core.functions import TransactionNow
from humotech.core.models import OrganizationScopedModel, UUIDPrimaryKeyModel


class AuditLogImmutable(RuntimeError):
    """Попытка изменить или удалить запись журнала через модель."""


class AuditLog(UUIDPrimaryKeyModel, OrganizationScopedModel, models.Model):
    # админа можно заблокировать или удалить — запись аудита обязана остаться
    actor_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="actor_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    actor_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="actor_employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=100)
    entity_id = models.UUIDField()
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    occurred_at = models.DateTimeField(db_default=TransactionNow())

    class Meta:
        db_table = "audit_logs"
        verbose_name = "запись аудита"
        verbose_name_plural = "журнал аудита"
        indexes = [
            models.Index(fields=["organization"], name="ix_audit_logs_organization_id"),
            # Сортировка по убыванию времени заложена в сам индекс: журнал
            # всегда читают с конца, и обратный порядок иначе стоил бы сортировки.
            models.Index(
                fields=["entity_type", "entity_id", "-occurred_at"],
                name="ix_audit_logs_entity",
            ),
            models.Index(
                fields=["actor_user", "-occurred_at"], name="ix_audit_logs_actor_user"
            ),
            models.Index(
                fields=["organization", "-occurred_at"], name="ix_audit_logs_org_time"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.entity_type}"

    def save(self, *args, **kwargs):
        """Только вставка, и только с замаскированными секретами.

        Маскирование здесь, а не у вызывающего: запись может прийти из
        любого сервиса, и полагаться на добросовестность каждого нельзя.
        Правка существующей строки отклоняется — журнал, который можно
        переписать тем же кодом, что делает изменения, ничего не доказывает.
        """
        if not self._state.adding:
            raise AuditLogImmutable("Запись журнала аудита не изменяется")
        from humotech.audit.redaction import redact_values

        self.old_values = redact_values(self.old_values)
        self.new_values = redact_values(self.new_values)
        if self.user_agent:
            self.user_agent = self.user_agent[:1000]
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogImmutable("Запись журнала аудита не удаляется")
