"""Очередь отправки поверх существующей таблицы уведомлений.

Отдельной таблицы outbox нет намеренно: уведомление и запись о том, что его
надо отправить, — это одно и то же, и раздваивать их значило бы заводить
второй источник правды.
"""

import django.db.models.expressions
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0001_initial'),
        ('notifications', '0001_initial'),
        ('organizations', '0001_initial'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='notification',
            name='ck_notifications_status',
        ),
        migrations.AddField(
            model_name='notification',
            name='attempts',
            field=models.IntegerField(db_default=0),
        ),
        migrations.AddField(
            model_name='notification',
            name='idempotency_key',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='notification',
            name='locked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='notification',
            name='next_attempt_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='notification',
            name='status',
            field=models.CharField(choices=[('PENDING', 'PENDING'), ('RUNNING', 'RUNNING'), ('SENT', 'SENT'), ('FAILED', 'FAILED'), ('CANCELLED', 'CANCELLED'), ('READ', 'READ')], max_length=20),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(condition=models.Q(('status', 'PENDING')), fields=['next_attempt_at'], name='ix_notifications_next_attempt'),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['PENDING', 'RUNNING', 'SENT', 'FAILED', 'CANCELLED', 'READ'])), name='ck_notifications_status'),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('attempts >= 0', [], output_field=models.BooleanField())), name='ck_notifications_attempts_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL("status <> 'RUNNING' OR locked_at IS NOT NULL", [], output_field=models.BooleanField())), name='ck_notifications_running_is_locked'),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.UniqueConstraint(condition=models.Q(('idempotency_key__isnull', False)), fields=('organization', 'idempotency_key'), name='uq_notifications_idempotency_key'),
        ),
    ]
