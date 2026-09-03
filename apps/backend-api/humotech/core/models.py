"""Базовые примеси моделей.

Повторяют правила, зашитые в прежнем `src/core/database/base.py`, чтобы схема
после переноса совпала с эталоном:

  * первичный ключ — UUID, значение генерирует PostgreSQL (`gen_random_uuid()`);
  * все временные метки — `TIMESTAMPTZ`, значения в UTC;
  * `organization_id` на большинстве таблиц — изоляция данных между организациями.

Имена ограничений и индексов задаются ЯВНО в каждой модели. Django умеет
придумывать их сам, но придумывает по-своему, а имена должны совпасть с теми,
что уже есть в базе: на них ссылается перевод ошибок целостности в понятные
сообщения (`humotech/core/errors.py`).
"""

from __future__ import annotations

from django.contrib.postgres.functions import RandomUUID
from django.db import models

from humotech.core.functions import TransactionNow


class UUIDPrimaryKeyModel(models.Model):
    """`id UUID PRIMARY KEY DEFAULT gen_random_uuid()`.

    Значение генерирует база, а не Python: строку могут вставить и миграцией,
    и обычным SQL, и во всех случаях ключ должен появиться.
    """

    id = models.UUIDField(
        primary_key=True,
        db_default=RandomUUID(),
        editable=False,
    )

    class Meta:
        abstract = True


class CreatedAtModel(models.Model):
    """Только `created_at` — для неизменяемых событий и журналов."""

    created_at = models.DateTimeField(db_default=TransactionNow(), editable=False)

    class Meta:
        abstract = True


class TimestampedModel(CreatedAtModel):
    """`created_at` + `updated_at` — для обычных изменяемых таблиц."""

    updated_at = models.DateTimeField(db_default=TransactionNow(), editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """Отметка времени изменения ставится приложением.

        Триггера в базе нет — его не было и в прежней схеме, там это делал
        SQLAlchemy через `onupdate`. Поле обновляется только при полном
        сохранении: частичное `update_fields` без `updated_at` трогать его
        не должно, иначе вызывающий не может сохранить одно поле, ничего
        больше не задев.
        """
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "updated_at" not in update_fields:
            return super().save(*args, **kwargs)
        from django.utils import timezone

        self.updated_at = timezone.now()
        return super().save(*args, **kwargs)


class ArchivableModel(models.Model):
    """`archived_at` вместо физического удаления."""

    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class OrganizationScopedModel(models.Model):
    """`organization_id NOT NULL` — корень изоляции данных.

    Индекс объявляется в конкретной модели с точным именем
    (`ix_<таблица>_organization_id`), поэтому здесь `db_index=False`:
    иначе Django создаст ещё один индекс со своим именем.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        db_column="organization_id",
        db_index=False,
        related_name="+",
    )

    class Meta:
        abstract = True
