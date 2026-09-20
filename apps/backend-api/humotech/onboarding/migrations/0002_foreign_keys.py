"""Внешние ключи ознакомления — в том же виде, что и остальные ключи схемы.

Django задаёт `on_delete` на стороне Python, а в DDL пишет
`DEFERRABLE INITIALLY DEFERRED` без `ON DELETE`. Запрет физического
удаления объектов с историей держится на `ON DELETE RESTRICT` в самой
базе: прямой `DELETE` мимо ORM обязан отклоняться. `core/0002` делает
это для остальных ключей — здесь повторён его приём.

`created_by_user_id` и `published_by_user_id` — единственные SET NULL:
учётную запись кадровика можно отключить, а карточка, документ и
выпущенная редакция от этого не исчезают. Всё остальное RESTRICT: под
подтверждением стоит имя сотрудника и время, и удалить карточку,
редакцию или самого человека мимо ORM нельзя.
"""

from django.db import migrations

FOREIGN_KEYS = [
    ('onboarding_programs', 'fk_onboarding_programs_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('onboarding_programs', 'fk_onboarding_programs_created_by_user_id',
     'created_by_user_id', 'users', 'id', 'SET NULL'),

    ('onboarding_sections', 'fk_onboarding_sections_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('onboarding_sections', 'fk_onboarding_sections_program_id',
     'program_id', 'onboarding_programs', 'id', 'RESTRICT'),

    ('employee_onboarding', 'fk_employee_onboarding_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_onboarding', 'fk_employee_onboarding_employee_id',
     'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_onboarding', 'fk_employee_onboarding_program_id',
     'program_id', 'onboarding_programs', 'id', 'RESTRICT'),
    ('employee_onboarding', 'fk_employee_onboarding_created_by_user_id',
     'created_by_user_id', 'users', 'id', 'SET NULL'),

    ('employee_onboarding_section_acks',
     'fk_onboarding_acks_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_onboarding_section_acks', 'fk_onboarding_acks_onboarding_id',
     'onboarding_id', 'employee_onboarding', 'id', 'RESTRICT'),
    ('employee_onboarding_section_acks', 'fk_onboarding_acks_section_id',
     'section_id', 'onboarding_sections', 'id', 'RESTRICT'),

    ('policy_documents', 'fk_policy_documents_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('policy_documents', 'fk_policy_documents_created_by_user_id',
     'created_by_user_id', 'users', 'id', 'SET NULL'),

    ('policy_document_versions', 'fk_policy_versions_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('policy_document_versions', 'fk_policy_versions_document_id',
     'document_id', 'policy_documents', 'id', 'RESTRICT'),
    ('policy_document_versions', 'fk_policy_versions_file_id',
     'file_id', 'files', 'id', 'RESTRICT'),
    ('policy_document_versions', 'fk_policy_versions_published_by_user_id',
     'published_by_user_id', 'users', 'id', 'SET NULL'),

    ('employee_policy_acceptances', 'fk_policy_acceptances_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_policy_acceptances', 'fk_policy_acceptances_employee_id',
     'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_policy_acceptances', 'fk_policy_acceptances_version_id',
     'version_id', 'policy_document_versions', 'id', 'RESTRICT'),
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
        ('onboarding', '0001_initial'),
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
