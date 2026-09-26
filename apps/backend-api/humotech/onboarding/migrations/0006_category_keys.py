"""Ключи разделов материалов — в том же виде, что и остальные (см. 0002).

Отдельной миграцией: Django добавляет свои ключи новой таблицы в конце
той миграции, где её создал, — уже после RunSQL, и ключи задвоились бы.
"""

import importlib

from django.db import migrations

_KEYS = importlib.import_module("humotech.onboarding.migrations.0002_foreign_keys")

FOREIGN_KEYS = [
    ('policy_categories', 'fk_policy_categories_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('policy_categories', 'fk_policy_categories_owner_employee_id',
     'owner_employee_id', 'employees', 'id', 'RESTRICT'),
    ('policy_categories', 'fk_policy_categories_created_by_user_id',
     'created_by_user_id', 'users', 'id', 'SET NULL'),
    ('policy_documents', 'fk_policy_documents_category_id',
     'category_id', 'policy_categories', 'id', 'RESTRICT'),
]

_VALUES = ",\n        ".join("('{}', '{}', '{}', '{}', '{}', '{}')".format(*row) for row in FOREIGN_KEYS)


class Migration(migrations.Migration):

    dependencies = [
        ("onboarding", "0005_categories_and_due"),
    ]

    operations = [
        migrations.RunSQL(sql=_KEYS._FORWARD % _VALUES, reverse_sql=_KEYS._BACKWARD % _VALUES),
    ]
