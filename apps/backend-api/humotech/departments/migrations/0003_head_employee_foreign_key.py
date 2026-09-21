"""`ON DELETE SET NULL` для руководителя отдела — в самой базе.

Django задаёт `on_delete` на стороне Python, а в DDL пишет
`DEFERRABLE INITIALLY DEFERRED` без `ON DELETE`. Остальные ключи схемы
приведены к явному поведению в `core/0002`; этот добавлен позже, и
правило для него объявляется здесь.

`SET NULL`, а не `RESTRICT`: отдел переживает своего руководителя.
Уволенного человека удаляют редко, но если его строку когда-нибудь
сотрут, отдел должен остаться без руководителя, а не исчезнуть следом.
"""

from django.db import migrations

NAME = "fk_departments_head_employee_id"

_FORWARD = f"""
DO $$
DECLARE existing text;
BEGIN
    -- Ключ ищем по колонке, а не по имени: имя ему дал Django и оно
    -- содержит хеш.
    SELECT c.conname INTO existing
      FROM pg_constraint c
     WHERE c.conrelid = 'departments'::regclass
       AND c.contype = 'f'
       AND pg_get_constraintdef(c.oid) LIKE 'FOREIGN KEY (head_employee_id)%';

    IF existing IS NOT NULL THEN
        EXECUTE format('ALTER TABLE departments DROP CONSTRAINT %I', existing);
    END IF;

    ALTER TABLE departments
        ADD CONSTRAINT {NAME}
        FOREIGN KEY (head_employee_id) REFERENCES employees (id)
        ON DELETE SET NULL;
END $$;
"""

_BACKWARD = f"""
ALTER TABLE departments DROP CONSTRAINT IF EXISTS {NAME};
ALTER TABLE departments
    ADD CONSTRAINT {NAME}
    FOREIGN KEY (head_employee_id) REFERENCES employees (id)
    DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("departments", "0002_head_and_description"),
        ("core", "0002_raw_schema_objects"),
    ]

    operations = [
        migrations.RunSQL(sql=_FORWARD, reverse_sql=_BACKWARD),
    ]
