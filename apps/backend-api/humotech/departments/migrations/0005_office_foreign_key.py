"""Вернуть `ON DELETE RESTRICT` ключу офиса у отдела.

`0004` сделал `office_id` необязательным. Django для этого пересоздаёт
внешний ключ — со своим именем и `DEFERRABLE INITIALLY DEFERRED`, без
`ON DELETE`. Правило, которое поставил `core/0002`, при этом теряется:
прямой `DELETE` офиса мимо ORM снова снёс бы отделы вместе с историей
назначений.

Запрет держится в самой базе, а не только в коллекторе Django, поэтому
ключ объявляется заново — тем же приёмом, что и остальные.
"""

from django.db import migrations

NAME = "fk_departments_office_id"

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
       AND pg_get_constraintdef(c.oid) LIKE 'FOREIGN KEY (office_id)%';

    IF existing IS NOT NULL THEN
        EXECUTE format('ALTER TABLE departments DROP CONSTRAINT %I', existing);
    END IF;

    ALTER TABLE departments
        ADD CONSTRAINT {NAME}
        FOREIGN KEY (office_id) REFERENCES offices (id)
        ON DELETE RESTRICT;
END $$;
"""

_BACKWARD = f"""
ALTER TABLE departments DROP CONSTRAINT IF EXISTS {NAME};
ALTER TABLE departments
    ADD CONSTRAINT {NAME}
    FOREIGN KEY (office_id) REFERENCES offices (id)
    DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):

    dependencies = [("departments", "0004_office_optional")]

    operations = [
        migrations.RunSQL(sql=_FORWARD, reverse_sql=_BACKWARD),
    ]
