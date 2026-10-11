"""Внешний ключ «регион графика» — в том же виде, что и остальные.

Django пишет ключ `DEFERRABLE INITIALLY DEFERRED` без `ON DELETE`; действие
нужно в самой базе. RESTRICT: регион, у которого есть графики, не удаляется
каскадом — график региона входит в историю отработанного времени.
Отдельной миграцией — чтобы ключ Django уже существовал к этому моменту.
"""

import importlib

from django.db import migrations

_KEYS = importlib.import_module("humotech.attendance.migrations.0004_day_notice_foreign_keys")

# (таблица, имя ключа, колонки, целевая таблица, целевые колонки, ON DELETE)
FOREIGN_KEYS = [
    ("work_schedules", "fk_work_schedules_region_id",
     "region_id", "regions", "id", "RESTRICT"),
]

_VALUES = ",\n        ".join(
    "('{}', '{}', '{}', '{}', '{}', '{}')".format(*row) for row in FOREIGN_KEYS
)


class Migration(migrations.Migration):

    dependencies = [
        ("schedules", "0004_work_schedule_region"),
        ("attendance", "0004_day_notice_foreign_keys"),
        ("core", "0002_raw_schema_objects"),
    ]

    operations = [
        migrations.RunSQL(sql=_KEYS._FORWARD % _VALUES, reverse_sql=_KEYS._BACKWARD % _VALUES),
    ]
