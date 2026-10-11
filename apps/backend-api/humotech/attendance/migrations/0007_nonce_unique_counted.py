"""Уникальность nonce — среди засчитанных отметок (ACCEPTED и REVIEW).

После M-9 отметка по пересланному сменному коду принимается со статусом
REVIEW. Прикладная проверка повтора учитывает оба статуса; ограничение
базы — второй рубеж — теперь тоже (независимая проверка 11.10, F-6).
"""

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [("attendance", "0006_field_work_foreign_keys")]

    operations = [
        migrations.RemoveConstraint(
            model_name="attendanceevent", name="uq_attendance_events_nonce",
        ),
        migrations.AddConstraint(
            model_name="attendanceevent",
            constraint=models.UniqueConstraint(
                fields=["employee", "qr_nonce_hash", "event_type"],
                condition=Q(qr_nonce_hash__isnull=False,
                            verification_status__in=("ACCEPTED", "REVIEW")),
                name="uq_attendance_events_nonce",
            ),
        ),
    ]
