"""Внешние ключи шаблонов отчётов — с `ON DELETE`, как и все остальные.

Django пишет в DDL `DEFERRABLE INITIALLY DEFERRED` без `ON DELETE`. Тот же
приём, что в `reports/0002` и `questions/0003`:

  * организация — RESTRICT: организацию с данными не удаляют;
  * владелец — CASCADE: шаблон — это личная настройка без истории,
    и пережить учётную запись ему незачем.
"""

from django.db import migrations

# (таблица, имя ключа, колонки, целевая таблица, целевые колонки, ON DELETE)
FOREIGN_KEYS = [
    ('report_templates', 'fk_report_templates_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('report_templates', 'fk_report_templates_owner_user_id',
     'owner_user_id', 'users', 'id', 'CASCADE'),
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
        ('reports', '0003_builder_templates'),
        ('core', '0002_raw_schema_objects'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_FORWARD % _values_sql(),
            reverse_sql=_BACKWARD % _values_sql(),
        ),
    ]
