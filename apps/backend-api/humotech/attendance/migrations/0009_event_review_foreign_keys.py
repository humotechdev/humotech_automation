"""Внешние ключи решений по подозрительным отметкам — в том же виде, что и остальные.

Организация и событие — RESTRICT: решение входит в историю отметки и не
должно исчезать вместе с чем-то удалённым мимо ORM. Автор решения —
SET NULL: удалённая учётка не уносит само решение.
"""

import importlib

from django.db import migrations

_KEYS = importlib.import_module("humotech.attendance.migrations.0004_day_notice_foreign_keys")

# (таблица, имя ключа, колонки, целевая таблица, целевые колонки, ON DELETE)
FOREIGN_KEYS = [
    ("attendance_event_reviews", "fk_attendance_event_reviews_organization_id",
     "organization_id", "organizations", "id", "RESTRICT"),
    ("attendance_event_reviews", "fk_attendance_event_reviews_event_id",
     "event_id", "attendance_events", "id", "RESTRICT"),
    ("attendance_event_reviews", "fk_attendance_event_reviews_decided_by_user_id",
     "decided_by_user_id", "users", "id", "SET NULL"),
]

_VALUES = ",\n        ".join(
    "('{}', '{}', '{}', '{}', '{}', '{}')".format(*row) for row in FOREIGN_KEYS
)


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0008_auto_exit_and_review"),
        ("core", "0002_raw_schema_objects"),
    ]

    operations = [
        migrations.RunSQL(sql=_KEYS._FORWARD % _VALUES, reverse_sql=_KEYS._BACKWARD % _VALUES),
    ]
