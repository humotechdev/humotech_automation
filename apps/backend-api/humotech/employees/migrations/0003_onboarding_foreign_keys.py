"""Внешние ключи приёма сотрудника — в том же виде, что и остальные ключи.

Django задаёт `on_delete` на стороне Python, а в DDL пишет
`DEFERRABLE INITIALLY DEFERRED` без всякого `ON DELETE`. Здесь так нельзя:
запрет физического удаления объектов с историей держится на `ON DELETE
RESTRICT` в самой базе, и прямой `DELETE` мимо ORM обязан отклоняться.
`core/0002` делает это для остальных ключей — здесь повторён его приём.

Все шесть ключей RESTRICT. Ни чек-лист документов, ни ключ идемпотентности
не должны исчезать вместе с чем-то удалённым мимо ORM: первый — часть
кадровой истории, второй — единственное доказательство того, что этот приём
уже состоялся, и без него повтор завёл бы второго человека.
"""

from django.db import migrations

# (таблица, имя ключа, колонки, целевая таблица, целевые колонки, ON DELETE)
FOREIGN_KEYS = [
    ('employee_documents', 'fk_employee_documents_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_documents', 'fk_employee_documents_employee_id',
     'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_documents', 'fk_employee_documents_file_id',
     'file_id', 'files', 'id', 'RESTRICT'),
    ('employee_onboarding_keys', 'fk_employee_onboarding_keys_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_onboarding_keys', 'fk_employee_onboarding_keys_employee_id',
     'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_onboarding_keys', 'fk_employee_onboarding_keys_created_by_user_id',
     'created_by_user_id', 'users', 'id', 'RESTRICT'),
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
        ('employees', '0002_employeedocument_employeeonboardingkey_and_more'),
        # Ключи переобъявляются ПОСЛЕ того, как core/0002 прошёлся по остальным:
        # иначе порядок применения на чистой базе зависел бы от случайности.
        ('core', '0002_raw_schema_objects'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_FORWARD % _values_sql(),
            reverse_sql=_BACKWARD % _values_sql(),
        ),
    ]
