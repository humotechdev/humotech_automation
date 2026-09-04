"""Экраны показа QR: устройство, его секреты и связь с сессией показа.

Ключи этой таблицы переобъявляются в 0003 — Django пишет их `DEFERRABLE`
и без `ON DELETE`, а вся схема держится на обратном.
"""

import django.contrib.postgres.functions
import django.db.models.deletion
import django.db.models.expressions
import humotech.core.functions
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0001_initial'),
        ('qr_codes', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='QrDisplayDevice',
            fields=[
                ('id', models.UUIDField(db_default=django.contrib.postgres.functions.RandomUUID(), editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(db_default=humotech.core.functions.TransactionNow(), editable=False)),
                ('updated_at', models.DateTimeField(db_default=humotech.core.functions.TransactionNow(), editable=False)),
                ('name', models.CharField(max_length=255)),
                ('status', models.CharField(choices=[('PENDING', 'PENDING'), ('ACTIVE', 'ACTIVE'), ('REVOKED', 'REVOKED')], max_length=20)),
                ('pairing_secret_hash', models.CharField(blank=True, max_length=64, null=True)),
                ('pairing_expires_at', models.DateTimeField(blank=True, null=True)),
                ('paired_at', models.DateTimeField(blank=True, null=True)),
                ('credential_hash', models.CharField(blank=True, max_length=64, null=True)),
                ('credential_issued_at', models.DateTimeField(blank=True, null=True)),
                ('credential_expires_at', models.DateTimeField(blank=True, null=True)),
                ('last_seen_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_by_user', models.ForeignKey(blank=True, db_column='created_by_user_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(db_column='organization_id', db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='organizations.organization')),
                ('qr_point', models.ForeignKey(db_column='qr_point_id', db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='display_devices', to='qr_codes.officeqrpoint')),
            ],
            options={
                'verbose_name': 'экран показа QR',
                'verbose_name_plural': 'экраны показа QR',
                'db_table': 'qr_display_devices',
            },
        ),
        migrations.AddField(
            model_name='qrdisplaysession',
            name='device',
            field=models.ForeignKey(blank=True, db_column='device_id', db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='sessions', to='qr_codes.qrdisplaydevice'),
        ),
        migrations.AddIndex(
            model_name='qrdisplaysession',
            index=models.Index(fields=['device'], name='ix_qr_display_sessions_device_id'),
        ),
        migrations.AddIndex(
            model_name='qrdisplaydevice',
            index=models.Index(fields=['organization'], name='ix_qr_display_devices_organization_id'),
        ),
        migrations.AddIndex(
            model_name='qrdisplaydevice',
            index=models.Index(fields=['qr_point'], name='ix_qr_display_devices_qr_point_id'),
        ),
        migrations.AddConstraint(
            model_name='qrdisplaydevice',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['PENDING', 'ACTIVE', 'REVOKED'])), name='ck_qr_display_devices_status'),
        ),
        migrations.AddConstraint(
            model_name='qrdisplaydevice',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL("status <> 'PENDING' OR pairing_secret_hash IS NOT NULL", [], output_field=models.BooleanField())), name='ck_qr_display_devices_pending_has_secret'),
        ),
        migrations.AddConstraint(
            model_name='qrdisplaydevice',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL("status <> 'ACTIVE' OR credential_hash IS NOT NULL", [], output_field=models.BooleanField())), name='ck_qr_display_devices_active_has_credential'),
        ),
        migrations.AddConstraint(
            model_name='qrdisplaydevice',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL("status <> 'REVOKED' OR (revoked_at IS NOT NULL AND credential_hash IS NULL)", [], output_field=models.BooleanField())), name='ck_qr_display_devices_revoked_is_disarmed'),
        ),
        migrations.AddConstraint(
            model_name='qrdisplaydevice',
            constraint=models.UniqueConstraint(condition=models.Q(('credential_hash__isnull', False)), fields=('credential_hash',), name='uq_qr_display_devices_credential'),
        ),
        migrations.AddConstraint(
            model_name='qrdisplaydevice',
            constraint=models.UniqueConstraint(condition=models.Q(('pairing_secret_hash__isnull', False)), fields=('pairing_secret_hash',), name='uq_qr_display_devices_pairing_secret'),
        ),
    ]
