"""Удаление функции «стажировка»: триггер опросов PROBATION_END.

Выполняется ДО сужения CHECK ck_survey_automations_trigger_kind: сначала
убрать правила с удаляемым триггером, потом снимать значение из набора —
иначе новое ограничение упало бы на существующих строках PROBATION_END.

Правила-автоматизации с триггером «Окончание стажировки» удаляются как
потерявшие смысл. Снимок кладётся в `survey_automations_probation_backup`
(не модель Django): ничего не теряется безвозвратно. Сами кампании и
ответы опросов НЕ трогаются.
"""

from django.db import migrations

FORWARD = """
CREATE TABLE IF NOT EXISTS survey_automations_probation_backup AS
SELECT *, now() AS backed_up_at FROM survey_automations WHERE trigger_kind = 'PROBATION_END';

DELETE FROM survey_automations WHERE trigger_kind = 'PROBATION_END';
"""

# Безопасного автоотката нет (строки удалены): снимок остаётся для ручного
# восстановления. Явно no-op, чтобы migrate --fake-reverse не падал.
REVERSE = migrations.RunSQL.noop


class Migration(migrations.Migration):

    dependencies = [
        ("surveys", "0006_anonymous_campaigns"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
