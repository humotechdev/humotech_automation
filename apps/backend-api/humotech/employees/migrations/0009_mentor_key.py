"""Ключ наставника — в том же виде, что и остальные ключи схемы.

Django пишет ключ `DEFERRABLE INITIALLY DEFERRED` без `ON DELETE`;
здесь он переобъявляется с RESTRICT, как у руководителя в назначении:
сотрудника, который у кого-то наставник, мимо ORM не удалить.
Отдельной миграцией — чтобы ключ Django уже существовал к этому моменту.
"""

import importlib

from django.db import migrations

_KEYS = importlib.import_module("humotech.onboarding.migrations.0002_foreign_keys")

FOREIGN_KEYS = [
    ('employees', 'fk_employees_mentor_employee_id',
     'mentor_employee_id', 'employees', 'id', 'RESTRICT'),
]

_VALUES = ",\n        ".join("('{}', '{}', '{}', '{}', '{}', '{}')".format(*row) for row in FOREIGN_KEYS)


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0008_probation_mentor"),
        ("onboarding", "0002_foreign_keys"),
    ]

    operations = [
        migrations.RunSQL(sql=_KEYS._FORWARD % _VALUES, reverse_sql=_KEYS._BACKWARD % _VALUES),
    ]
