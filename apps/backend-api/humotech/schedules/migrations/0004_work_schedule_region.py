"""График принадлежит организации (region = NULL) или региону.

Существующие графики остаются графиками организации: так их и
использовали, и права на них не расширяются никому.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("regions", "0001_initial"),
        ("schedules", "0003_calendar_exception_is_active"),
    ]

    operations = [
        migrations.AddField(
            model_name="workschedule",
            name="region",
            field=models.ForeignKey(
                blank=True, db_column="region_id", null=True,
                on_delete=django.db.models.deletion.PROTECT, related_name="+",
                to="regions.region",
            ),
        ),
        migrations.AddIndex(
            model_name="workschedule",
            index=models.Index(fields=["region"], name="ix_work_schedules_region_id"),
        ),
    ]
