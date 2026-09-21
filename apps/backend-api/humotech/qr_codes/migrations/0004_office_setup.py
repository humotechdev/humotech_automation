"""Настройка офиса из CRM: описание точки, её автор и время перевыпуска.

Автор — единственный новый внешний ключ, и он SET NULL: учётную запись HR
можно отключить, а точка у двери от этого работать не перестаёт. Django
пишет ключ без `ON DELETE` и отложенным, а схема проекта требует
действия в самой базе, поэтому ключ переобъявляется тем же приёмом, что
в `0003_display_device_foreign_keys`.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

FOREIGN_KEY = (
    'office_qr_points', 'fk_office_qr_points_created_by_user_id',
    'created_by_user_id', 'users', 'id', 'SET NULL',
)

_FORWARD = """
DO $$
DECLARE existing text;
BEGIN
    SELECT c.conname INTO existing
      FROM pg_constraint c
     WHERE c.conrelid = '{0}'::regclass
       AND c.contype = 'f'
       AND pg_get_constraintdef(c.oid) LIKE 'FOREIGN KEY ({2})%';

    IF existing IS NOT NULL THEN
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', '{0}', existing);
    END IF;

    ALTER TABLE {0} ADD CONSTRAINT {1}
        FOREIGN KEY ({2}) REFERENCES {3} ({4}) ON DELETE {5};
END $$;
""".format(*FOREIGN_KEY)

_BACKWARD = """
ALTER TABLE {0} DROP CONSTRAINT IF EXISTS {1};
ALTER TABLE {0} ADD CONSTRAINT {1}
    FOREIGN KEY ({2}) REFERENCES {3} ({4}) DEFERRABLE INITIALLY DEFERRED;
""".format(*FOREIGN_KEY)


class Migration(migrations.Migration):

    dependencies = [
        ('qr_codes', '0003_display_device_foreign_keys'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='officeqrpoint',
            name='created_by_user',
            field=models.ForeignKey(blank=True, db_column='created_by_user_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='officeqrpoint',
            name='description',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='officeqrpoint',
            name='rotated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunSQL(sql=_FORWARD, reverse_sql=_BACKWARD),
    ]
