"""Внешний ключ «кто предложил новое место офиса» — в том же виде, что и остальные.

Django задаёт `on_delete` на стороне Python, а в DDL пишет
`DEFERRABLE INITIALLY DEFERRED` без всякого `ON DELETE`. Действие нужно в
самой базе: прямой `DELETE` мимо ORM должен вести себя так же, как ORM.

SET NULL, как у автора QR-точки: учётную запись HR можно удалить, а
предложенное место остаётся и ждёт решения — пропадает только подпись.

Отдельной миграцией — чтобы ключ Django уже существовал к этому моменту.
"""

import importlib

from django.db import migrations

_KEYS = importlib.import_module("humotech.attendance.migrations.0004_day_notice_foreign_keys")

# (таблица, имя ключа, колонки, целевая таблица, целевые колонки, ON DELETE)
FOREIGN_KEYS = [
    ('offices', 'fk_offices_pending_location_by_user_id',
     'pending_location_by_user_id', 'users', 'id', 'SET NULL'),
]

_VALUES = ",\n        ".join(
    "('{}', '{}', '{}', '{}', '{}', '{}')".format(*row) for row in FOREIGN_KEYS
)


class Migration(migrations.Migration):

    dependencies = [
        ("offices", "0004_office_pending_location"),
        ("attendance", "0004_day_notice_foreign_keys"),
        ("core", "0002_raw_schema_objects"),
    ]

    operations = [
        migrations.RunSQL(sql=_KEYS._FORWARD % _VALUES, reverse_sql=_KEYS._BACKWARD % _VALUES),
    ]
