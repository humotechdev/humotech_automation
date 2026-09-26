"""Внешние ключи автоматизаций — в том же виде, что и остальные ключи.

Django задаёт `on_delete` на стороне Python, а в DDL пишет
`DEFERRABLE INITIALLY DEFERRED` без `ON DELETE`. Запрет физического
удаления объектов с историей держится на действии в самой базе: прямой
`DELETE` мимо ORM обязан отклоняться. `surveys/0002` сделал это для
первых пяти таблиц — здесь повторён тот же приём для трёх новых ключей
правила и двух ссылок, появившихся у шаблона и рассылки.

RESTRICT у организации и шаблона: правило — часть кадровой истории, а
шаблон, по которому рассылают, удалять из-под него нельзя. SET NULL у
автора, у прежней редакции и у правила-родителя: учётную запись
кадровика можно отключить, а правило — удалить, и ни рассылка, ни её
ответы от этого исчезнуть не должны.
"""

from django.db import migrations

FOREIGN_KEYS = [
    ('survey_automations', 'fk_survey_automations_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('survey_automations', 'fk_survey_automations_template_id',
     'template_id', 'survey_templates', 'id', 'RESTRICT'),
    ('survey_automations', 'fk_survey_automations_created_by_user_id',
     'created_by_user_id', 'users', 'id', 'SET NULL'),
    ('survey_templates', 'fk_survey_templates_previous_version_id',
     'previous_version_id', 'survey_templates', 'id', 'SET NULL'),
    ('survey_campaigns', 'fk_survey_campaigns_automation_id',
     'automation_id', 'survey_automations', 'id', 'SET NULL'),
]

_FORWARD = """
DO $$
DECLARE
    r record;
    existing text;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        %s
    ) AS t(tbl, name, cols, ref_tbl, ref_cols, action)
    LOOP
        -- Существующий ключ ищем по таблице и колонкам, а не по имени:
        -- имя ему дал Django и оно содержит хеш.
        SELECT c.conname INTO existing
          FROM pg_constraint c
         WHERE c.conrelid = r.tbl::regclass
           AND c.contype = 'f'
           AND pg_get_constraintdef(c.oid) LIKE 'FOREIGN KEY (' || r.cols || ')%%';

        IF existing IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %%I DROP CONSTRAINT %%I', r.tbl, existing);
        END IF;

        EXECUTE format(
            'ALTER TABLE %%I ADD CONSTRAINT %%I FOREIGN KEY (%%s) '
            'REFERENCES %%I (%%s) ON DELETE %%s',
            r.tbl, r.name, r.cols, r.ref_tbl, r.ref_cols, r.action);
    END LOOP;
END $$;
"""

_BACKWARD = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        %s
    ) AS t(tbl, name, cols, ref_tbl, ref_cols, action)
    LOOP
        EXECUTE format('ALTER TABLE %%I DROP CONSTRAINT IF EXISTS %%I', r.tbl, r.name);
        EXECUTE format(
            'ALTER TABLE %%I ADD CONSTRAINT %%I FOREIGN KEY (%%s) '
            'REFERENCES %%I (%%s) DEFERRABLE INITIALLY DEFERRED',
            r.tbl, r.name, r.cols, r.ref_tbl, r.ref_cols);
    END LOOP;
END $$;
"""


def _values_sql() -> str:
    return ",\n        ".join(
        "('{}', '{}', '{}', '{}', '{}', '{}')".format(*row)
        for row in FOREIGN_KEYS
    )


class Migration(migrations.Migration):

    dependencies = [
        ('surveys', '0003_automations_and_versions'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_FORWARD % _values_sql(),
            reverse_sql=_BACKWARD % _values_sql(),
        ),
    ]
