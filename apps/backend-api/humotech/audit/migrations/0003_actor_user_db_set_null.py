"""Ссылку на удалённую учётку в журнале обнуляет база, а не ORM.

Изменение только на стороне Python (`on_delete`): в базе ключ уже
`ON DELETE SET NULL` (core/0002), DDL не меняется. Нужно, чтобы удаление
учётки не упиралось в триггер append-only журнала (audit/0002): прямой
UPDATE из приложения он отклоняет, а действие внешнего ключа пропускает.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_audit_logs_append_only"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="actor_user",
            field=models.ForeignKey(
                blank=True, db_column="actor_user_id", db_index=False, null=True,
                on_delete=django.db.models.deletion.DO_NOTHING, related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
