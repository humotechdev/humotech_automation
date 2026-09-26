"""Срок стажировки: с какого по какое число.

Сам факт стажировки живёт в `employment_status` (значение PROBATION) —
второго места для той же мысли заводить нельзя, они разошлись бы. Но
статус не отвечает на вопрос «до какого числа», а его задают в первый же
месяц: кадровику нужно знать, когда принимать решение по человеку.

Обе даты необязательны, и по отдельности тоже: стажёра берут и тогда,
когда конец ещё не назван. «Взяли стажёром» и «стажируется до такого-то»
— разные утверждения, и пустая дата означает первое, а не ошибку.

Ограничение в базе, а не только в сервисе: даты правятся не одним путём
— приёмом и правкой карточки, — и запись, где стажировка кончается
раньше, чем началась, не должна существовать ни при каком порядке
вызовов.
"""

from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0006_termination_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="probation_from",
            field=models.DateField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="employee",
            name="probation_to",
            field=models.DateField(null=True, blank=True),
        ),
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.CheckConstraint(
                condition=(
                    Q(probation_from__isnull=True)
                    | Q(probation_to__isnull=True)
                    | Q(probation_to__gte=F("probation_from"))
                ),
                name="ck_employees_probation_order",
            ),
        ),
    ]
