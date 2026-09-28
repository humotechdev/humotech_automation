"""Внешние ключи запросов выездной работы — в том же виде, что и остальные.

Django задаёт `on_delete` на стороне Python, а в DDL пишет
`DEFERRABLE INITIALLY DEFERRED` без всякого `ON DELETE`. Запрет
физического удаления объектов с историей держится на `ON DELETE RESTRICT`
в самой базе, и прямой `DELETE` мимо ORM обязан отклоняться.

Все четыре ключа RESTRICT, как `PROTECT` в модели. Запрос выездной работы —
основание зачёта рабочего дня: он не должен исчезать вместе с офисом,
сотрудником или учётной записью HR, удалёнными мимо ORM.

Отдельной миграцией — чтобы ключи Django уже существовали к этому моменту.
"""

import importlib

from django.db import migrations

_KEYS = importlib.import_module("humotech.attendance.migrations.0004_day_notice_foreign_keys")

# (таблица, имя ключа, колонки, целевая таблица, целевые колонки, ON DELETE)
FOREIGN_KEYS = [
    ('attendance_field_work_requests', 'fk_attendance_field_work_requests_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('attendance_field_work_requests', 'fk_attendance_field_work_requests_employee_id',
     'employee_id', 'employees', 'id', 'RESTRICT'),
    ('attendance_field_work_requests', 'fk_attendance_field_work_requests_office_id',
     'office_id', 'offices', 'id', 'RESTRICT'),
    ('attendance_field_work_requests', 'fk_attendance_field_work_requests_requested_by_user_id',
     'requested_by_user_id', 'users', 'id', 'RESTRICT'),
]

_VALUES = ",\n        ".join(
    "('{}', '{}', '{}', '{}', '{}', '{}')".format(*row) for row in FOREIGN_KEYS
)


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0005_field_work"),
        ("core", "0002_raw_schema_objects"),
    ]

    operations = [
        migrations.RunSQL(sql=_KEYS._FORWARD % _VALUES, reverse_sql=_KEYS._BACKWARD % _VALUES),
    ]
