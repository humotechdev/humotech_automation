"""Внешние ключи версий графика — в том же виде, что и остальные.

`version_of` — CASCADE: версия не живёт без своего графика (график
удаляется только пока его никому не назначали). `changed_by_user` —
SET NULL: удалённая учётка не должна уносить историю изменений графика.
"""

import importlib

from django.db import migrations

_KEYS = importlib.import_module("humotech.attendance.migrations.0004_day_notice_foreign_keys")

# (таблица, имя ключа, колонки, целевая таблица, целевые колонки, ON DELETE)
FOREIGN_KEYS = [
    ("work_schedules", "fk_work_schedules_version_of_id",
     "version_of_id", "work_schedules", "id", "CASCADE"),
    ("work_schedules", "fk_work_schedules_changed_by_user_id",
     "changed_by_user_id", "users", "id", "SET NULL"),
]

_VALUES = ",\n        ".join(
    "('{}', '{}', '{}', '{}', '{}', '{}')".format(*row) for row in FOREIGN_KEYS
)


class Migration(migrations.Migration):

    dependencies = [
        ("schedules", "0006_work_schedule_versions"),
        ("attendance", "0004_day_notice_foreign_keys"),
        ("core", "0002_raw_schema_objects"),
    ]

    operations = [
        migrations.RunSQL(sql=_KEYS._FORWARD % _VALUES, reverse_sql=_KEYS._BACKWARD % _VALUES),
    ]
