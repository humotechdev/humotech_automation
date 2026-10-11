"""Автовыход и решения по подозрительным отметкам (решения владельца Р-1, Р-3).

* Статус сессии AUTO_CLOSED и поля `closure_kind`, `unconfirmed_seconds`,
  `auto_closed_at`. Существующие сессии не меняются: открытые закроет
  задача автовыхода (см. `attendance_auto_close --dry-run` перед выкаткой).
* Таблица `attendance_event_reviews`: одно решение HR на REVIEW-событие.
"""


import django.contrib.postgres.functions
import django.db.models.deletion
import django.db.models.expressions
import humotech.core.functions
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0007_nonce_unique_counted'),
        ('employees', '0011_remove_employee_ck_employees_employment_status_and_more'),
        ('offices', '0005_pending_location_foreign_key'),
        ('organizations', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AttendanceEventReview',
            fields=[
                ('id', models.UUIDField(db_default=django.contrib.postgres.functions.RandomUUID(), editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(db_default=humotech.core.functions.TransactionNow(), editable=False)),
                ('decision', models.CharField(choices=[('CONFIRM', 'CONFIRM'), ('ANNUL', 'ANNUL')], max_length=20)),
                ('reason', models.TextField()),
                ('decided_at', models.DateTimeField()),
            ],
            options={
                'verbose_name': 'решение по подозрительной отметке',
                'verbose_name_plural': 'решения по подозрительным отметкам',
                'db_table': 'attendance_event_reviews',
            },
        ),
        migrations.RemoveConstraint(
            model_name='attendancesession',
            name='ck_attendance_sessions_status',
        ),
        migrations.AddField(
            model_name='attendancesession',
            name='auto_closed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='attendancesession',
            name='closure_kind',
            field=models.CharField(blank=True, choices=[('AUTO_MIDNIGHT', 'AUTO_MIDNIGHT'), ('EXIT_ANNULLED', 'EXIT_ANNULLED')], max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='attendancesession',
            name='unconfirmed_seconds',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='attendancesession',
            name='status',
            field=models.CharField(choices=[('OPEN', 'OPEN'), ('CLOSED', 'CLOSED'), ('CORRECTED', 'CORRECTED'), ('INVALID', 'INVALID'), ('AUTO_CLOSED', 'AUTO_CLOSED')], max_length=20),
        ),
        migrations.AddConstraint(
            model_name='attendancesession',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['OPEN', 'CLOSED', 'CORRECTED', 'INVALID', 'AUTO_CLOSED'])), name='ck_attendance_sessions_status'),
        ),
        migrations.AddConstraint(
            model_name='attendancesession',
            constraint=models.CheckConstraint(condition=models.Q(('closure_kind__isnull', True), ('closure_kind__in', ['AUTO_MIDNIGHT', 'EXIT_ANNULLED']), _connector='OR'), name='ck_attendance_sessions_closure_kind'),
        ),
        migrations.AddConstraint(
            model_name='attendancesession',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL("status <> 'AUTO_CLOSED' OR (ended_at IS NOT NULL AND duration_seconds = 0 AND closure_kind IS NOT NULL)", [], output_field=models.BooleanField())), name='ck_attendance_sessions_auto_closed_shape'),
        ),
        migrations.AddConstraint(
            model_name='attendancesession',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('unconfirmed_seconds IS NULL OR unconfirmed_seconds >= 0', [], output_field=models.BooleanField())), name='ck_attendance_sessions_unconfirmed_non_negative'),
        ),
        migrations.AddField(
            model_name='attendanceeventreview',
            name='decided_by_user',
            field=models.ForeignKey(blank=True, db_column='decided_by_user_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='attendanceeventreview',
            name='event',
            field=models.OneToOneField(db_column='event_id', on_delete=django.db.models.deletion.PROTECT, related_name='review', to='attendance.attendanceevent'),
        ),
        migrations.AddField(
            model_name='attendanceeventreview',
            name='organization',
            field=models.ForeignKey(db_column='organization_id', db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='organizations.organization'),
        ),
        migrations.AddIndex(
            model_name='attendanceeventreview',
            index=models.Index(fields=['organization'], name='ix_attendance_event_reviews_org_id'),
        ),
        migrations.AddConstraint(
            model_name='attendanceeventreview',
            constraint=models.CheckConstraint(condition=models.Q(('decision__in', ['CONFIRM', 'ANNUL'])), name='ck_attendance_event_reviews_decision'),
        ),
    ]
