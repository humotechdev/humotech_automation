"""Причина увольнения — отдельным полем, а не только в журнале аудита.

До сих пор причина уходила в `audit_logs` и больше нигде не жила. Для
вопроса «почему человека нет в штате» этого мало: журнал читают, когда
разбираются в спорной ситуации, а причину видно должно быть сразу в
карточке — особенно «не прошёл стажировку», решение по которой
принимают раз в месяц и вспоминают через год.

Свободный текст, а не перечисление: причин столько же, сколько
обстоятельств, и загонять их в список значит однажды выбрать «прочее»
там, где важна была формулировка.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0005_photo_foreign_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="termination_reason",
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
    ]
