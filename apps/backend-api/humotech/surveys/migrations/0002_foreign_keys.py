"""Внешние ключи опросов — в том же виде, что и остальные ключи схемы.

Django задаёт `on_delete` на стороне Python, а в DDL пишет
`DEFERRABLE INITIALLY DEFERRED` без `ON DELETE`. Запрет физического
удаления объектов с историей держится на `ON DELETE RESTRICT` в самой
базе: прямой `DELETE` мимо ORM обязан отклоняться. `core/0002` делает
это для остальных ключей — здесь повторён его приём.

`created_by_user_id` — единственные SET NULL: учётную запись HR можно
отключить, а шаблон и рассылка от этого не исчезают. Остальное RESTRICT:
шаблон с ответами, рассылку с получателями и вопрос, на который уже
ответили, удалить нельзя.
"""

from django.db import migrations

FOREIGN_KEYS = [
    ('survey_templates', 'fk_survey_templates_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('survey_templates', 'fk_survey_templates_created_by_user_id',
     'created_by_user_id', 'users', 'id', 'SET NULL'),
    ('survey_questions', 'fk_survey_questions_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('survey_questions', 'fk_survey_questions_template_id',
     'template_id', 'survey_templates', 'id', 'RESTRICT'),
    ('survey_campaigns', 'fk_survey_campaigns_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('survey_campaigns', 'fk_survey_campaigns_template_id',
     'template_id', 'survey_templates', 'id', 'RESTRICT'),
    ('survey_campaigns', 'fk_survey_campaigns_created_by_user_id',
     'created_by_user_id', 'users', 'id', 'SET NULL'),
    ('survey_recipients', 'fk_survey_recipients_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('survey_recipients', 'fk_survey_recipients_campaign_id',
     'campaign_id', 'survey_campaigns', 'id', 'RESTRICT'),
    ('survey_recipients', 'fk_survey_recipients_employee_id',
     'employee_id', 'employees', 'id', 'RESTRICT'),
    ('survey_answers', 'fk_survey_answers_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('survey_answers', 'fk_survey_answers_recipient_id',
     'recipient_id', 'survey_recipients', 'id', 'RESTRICT'),
    ('survey_answers', 'fk_survey_answers_question_id',
     'question_id', 'survey_questions', 'id', 'RESTRICT'),
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
        ('surveys', '0001_initial'),
        # Ключи переобъявляются ПОСЛЕ того, как core/0002 прошёлся по
        # остальным: иначе порядок применения на чистой базе зависел бы
        # от случайности.
        ('core', '0002_raw_schema_objects'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_FORWARD % _values_sql(),
            reverse_sql=_BACKWARD % _values_sql(),
        ),
    ]
