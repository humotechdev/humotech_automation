"""Удаление функции «стажировка»: триггер опросов PROBATION_END.

Выполняется ДО сужения CHECK ck_survey_automations_trigger_kind: сначала
убрать правила с удаляемым триггером, потом снимать значение из набора —
иначе новое ограничение упало бы на существующих строках PROBATION_END.

Правила-автоматизации с триггером «Окончание стажировки» удаляются как
потерявшие смысл. Снимок в `survey_automations_probation_backup` делается
ТОЛЬКО если такие правила есть (на чистой установке лишней таблицы не
создаётся). Кампании и ответы опросов НЕ трогаются.
"""

from django.db import migrations

FORWARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM survey_automations WHERE trigger_kind = 'PROBATION_END') THEN
        CREATE TABLE IF NOT EXISTS survey_automations_probation_backup AS
        SELECT *, now() AS backed_up_at FROM survey_automations WHERE trigger_kind = 'PROBATION_END';
    END IF;
END $$;

DELETE FROM survey_automations WHERE trigger_kind = 'PROBATION_END';
"""

REVERSE = migrations.RunSQL.noop


class Migration(migrations.Migration):

    dependencies = [
        ("surveys", "0006_anonymous_campaigns"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
