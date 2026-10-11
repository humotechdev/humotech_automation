"""Версии рабочего графика «с даты» (решение владельца Р-5, аудит C-10).

Существующие графики становятся первой версией самих себя: `valid_from`
пуст (действует с начала), `version_of` пуст. Ни одна существующая строка
не меняется, история расчётов прежняя.
"""


import django.contrib.postgres.constraints
import django.contrib.postgres.fields.ranges
import django.db.models.deletion
import django.db.models.expressions
import django.db.models.functions.comparison
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0001_initial'),
        ('regions', '0001_initial'),
        ('schedules', '0005_work_schedule_region_foreign_key'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='workschedule',
            name='change_reason',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workschedule',
            name='changed_by_user',
            field=models.ForeignKey(blank=True, db_column='changed_by_user_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='workschedule',
            name='is_retroactive',
            field=models.BooleanField(db_default=False),
        ),
        migrations.AddField(
            model_name='workschedule',
            name='valid_from',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workschedule',
            name='valid_to',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workschedule',
            name='version_of',
            field=models.ForeignKey(blank=True, db_column='version_of_id', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='schedules.workschedule'),
        ),
        migrations.AddIndex(
            model_name='workschedule',
            index=models.Index(fields=['version_of'], name='ix_work_schedules_version_of_id'),
        ),
        migrations.AddConstraint(
            model_name='workschedule',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from', [], output_field=models.BooleanField())), name='ck_work_schedules_version_period'),
        ),
        migrations.AddConstraint(
            model_name='workschedule',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('version_of_id IS NULL OR valid_from IS NOT NULL', [], output_field=models.BooleanField())), name='ck_work_schedules_version_has_start'),
        ),
        migrations.AddConstraint(
            model_name='workschedule',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('version_of_id IS NULL OR version_of_id <> id', [], output_field=models.BooleanField())), name='ck_work_schedules_version_not_self'),
        ),
        migrations.AddConstraint(
            model_name='workschedule',
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(expressions=[(django.db.models.functions.comparison.Coalesce('version_of', 'id'), '='), (models.Func(models.F('valid_from'), models.F('valid_to'), models.Value('[]'), function='daterange', output_field=django.contrib.postgres.fields.ranges.DateRangeField()), '&&')], name='ex_work_schedules_version_overlap'),
        ),
    ]
