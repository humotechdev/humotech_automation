"""Опросы: редакции шаблона, автоматизации, исходы получателя.

Шаблон получает состояние и номер редакции. Опубликованный больше не
правится на месте: по нему уже спрашивали людей, и переписанный вопрос
сделал бы прежние ответы ответами на другой вопрос. Правка создаёт
новую редакцию, а рассылка запоминает у себя, по какой из них
спрашивали.

`survey_automations` — третья сущность рядом с шаблоном и рассылкой.
Шаблон отвечает «что спрашивают», рассылка — «кого спросили в тот
день», автоматизация — «почему спросят завтра». Свести её с рассылкой
нельзя: у правила нет получателей и не будет до самого события.

У получателя появляются два исхода — `SKIPPED` и `EXPIRED`. Без них
строка человека без Telegram висела бы в «отправляем» вечно и портила
бы счёт по всей рассылке.
"""

import django.contrib.postgres.functions
import django.db.models.deletion
import django.db.models.expressions
import humotech.core.functions
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0007_probation_period'),
        ('organizations', '0001_initial'),
        ('surveys', '0002_foreign_keys'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SurveyAutomation',
            fields=[
                ('id', models.UUIDField(db_default=django.contrib.postgres.functions.RandomUUID(), editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(db_default=humotech.core.functions.TransactionNow(), editable=False)),
                ('updated_at', models.DateTimeField(db_default=humotech.core.functions.TransactionNow(), editable=False)),
                ('title', models.CharField(max_length=255)),
                ('trigger_kind', models.CharField(choices=[('PROBATION_END', 'PROBATION_END'), ('FIRST_DAY', 'FIRST_DAY'), ('DAYS_AFTER_HIRE', 'DAYS_AFTER_HIRE'), ('BIRTHDAY', 'BIRTHDAY'), ('SCHEDULE', 'SCHEDULE')], max_length=20)),
                ('offset_days', models.IntegerField(db_default=0)),
                ('send_hour', models.IntegerField(db_default=10)),
                ('send_minute', models.IntegerField(db_default=0)),
                ('repeat_months', models.IntegerField(blank=True, null=True)),
                ('scope', models.JSONField(blank=True, null=True)),
                ('is_active', models.BooleanField(db_default=True)),
                ('last_run_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'автоматизация опроса',
                'verbose_name_plural': 'автоматизации опросов',
                'db_table': 'survey_automations',
            },
        ),
        migrations.RemoveConstraint(
            model_name='surveyrecipient',
            name='ck_survey_recipients_status',
        ),
        migrations.AddField(
            model_name='surveycampaign',
            name='due_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surveycampaign',
            name='remind_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surveycampaign',
            name='reminded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surveycampaign',
            name='template_version',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surveycampaign',
            name='trigger_key',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='surveyrecipient',
            name='skip_reason',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='surveytemplate',
            name='previous_version',
            field=models.ForeignKey(blank=True, db_column='previous_version_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='surveys.surveytemplate'),
        ),
        migrations.AddField(
            model_name='surveytemplate',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surveytemplate',
            name='status',
            field=models.CharField(choices=[('DRAFT', 'DRAFT'), ('PUBLISHED', 'PUBLISHED'), ('ARCHIVED', 'ARCHIVED')], db_default='DRAFT', max_length=20),
        ),
        migrations.AddField(
            model_name='surveytemplate',
            name='version',
            field=models.IntegerField(db_default=1),
        ),
        migrations.AlterField(
            model_name='surveyrecipient',
            name='status',
            field=models.CharField(choices=[('PENDING', 'PENDING'), ('SENT', 'SENT'), ('STARTED', 'STARTED'), ('COMPLETED', 'COMPLETED'), ('SKIPPED', 'SKIPPED'), ('EXPIRED', 'EXPIRED')], max_length=20),
        ),
        migrations.AddConstraint(
            model_name='surveyrecipient',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['PENDING', 'SENT', 'STARTED', 'COMPLETED', 'SKIPPED', 'EXPIRED'])), name='ck_survey_recipients_status'),
        ),
        migrations.AddConstraint(
            model_name='surveytemplate',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['DRAFT', 'PUBLISHED', 'ARCHIVED'])), name='ck_survey_templates_status'),
        ),
        migrations.AddConstraint(
            model_name='surveytemplate',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('version > 0', [], output_field=models.BooleanField())), name='ck_survey_templates_version_positive'),
        ),
        migrations.AddConstraint(
            model_name='surveytemplate',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL("status <> 'PUBLISHED' OR published_at IS NOT NULL", [], output_field=models.BooleanField())), name='ck_survey_templates_published_has_time'),
        ),
        migrations.AddField(
            model_name='surveyautomation',
            name='created_by_user',
            field=models.ForeignKey(blank=True, db_column='created_by_user_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='surveyautomation',
            name='organization',
            field=models.ForeignKey(db_column='organization_id', db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='organizations.organization'),
        ),
        migrations.AddField(
            model_name='surveyautomation',
            name='template',
            field=models.ForeignKey(db_column='template_id', db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='automations', to='surveys.surveytemplate'),
        ),
        migrations.AddField(
            model_name='surveycampaign',
            name='automation',
            field=models.ForeignKey(blank=True, db_column='automation_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='campaigns', to='surveys.surveyautomation'),
        ),
        migrations.AddConstraint(
            model_name='surveycampaign',
            constraint=models.UniqueConstraint(condition=models.Q(('trigger_key__isnull', False)), fields=['organization', 'automation', 'trigger_key'], name='uq_survey_campaigns_occasion'),
        ),
        migrations.AddIndex(
            model_name='surveyautomation',
            index=models.Index(fields=['organization'], name='ix_survey_automations_organization_id'),
        ),
        migrations.AddIndex(
            model_name='surveyautomation',
            index=models.Index(fields=['template'], name='ix_survey_automations_template_id'),
        ),
        migrations.AddIndex(
            model_name='surveyautomation',
            index=models.Index(condition=models.Q(('is_active', True)), fields=['is_active'], name='ix_survey_automations_active'),
        ),
        migrations.AddConstraint(
            model_name='surveyautomation',
            constraint=models.CheckConstraint(condition=models.Q(('trigger_kind__in', ['PROBATION_END', 'FIRST_DAY', 'DAYS_AFTER_HIRE', 'BIRTHDAY', 'SCHEDULE'])), name='ck_survey_automations_trigger_kind'),
        ),
        migrations.AddConstraint(
            model_name='surveyautomation',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('send_hour BETWEEN 0 AND 23', [], output_field=models.BooleanField())), name='ck_survey_automations_send_hour_range'),
        ),
        migrations.AddConstraint(
            model_name='surveyautomation',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('send_minute BETWEEN 0 AND 59', [], output_field=models.BooleanField())), name='ck_survey_automations_send_minute_range'),
        ),
        migrations.AddConstraint(
            model_name='surveyautomation',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('offset_days >= 0', [], output_field=models.BooleanField())), name='ck_survey_automations_offset_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='surveyautomation',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL('repeat_months IS NULL OR repeat_months > 0', [], output_field=models.BooleanField())), name='ck_survey_automations_repeat_positive'),
        ),
        migrations.AddConstraint(
            model_name='surveyautomation',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.expressions.RawSQL("(trigger_kind = 'SCHEDULE' AND repeat_months IS NOT NULL) OR (trigger_kind <> 'SCHEDULE' AND repeat_months IS NULL)", [], output_field=models.BooleanField())), name='ck_survey_automations_repeat_matches_kind'),
        ),
    ]
