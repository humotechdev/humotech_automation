"""Два инварианта отсутствий — в саму базу.

`ex_employee_absences_overlap` — у сотрудника не может быть двух живых
отсутствий, делящих хотя бы одни сутки. Проверка в сервисе остаётся и
никуда не денется: только она умеет объяснить человеку, ЧТО именно он
перекрывает. Но живёт она в коде, а таблицу правят ещё и миграции,
импорт из старой системы и любой будущий сервис, — и последнее слово
должно быть за базой.

Диапазон берётся с ВКЛЮЧИТЕЛЬНЫМИ границами (`'[]'`): `end_at` у
отсутствия хранится последней микросекундой последнего дня, а не
началом следующего. Полуинтервал отрезал бы её, и 1–5 октября с 5–10
октября разошлись бы на микросекунду — то есть общий день перестал бы
считаться пересечением.

`MARKS_OVERRIDDEN` — кадровик подтвердил больничный, зная, что в эти
дни есть отметки входа и выхода. Отдельное действие истории: «утвердил»
и «утвердил поверх отметок» — разные решения, и второе должно быть
видно само по себе, вместе с причиной.
"""

import django.contrib.postgres.constraints
import django.contrib.postgres.fields.ranges
from django.db import migrations, models

ACTIONS = [
    'CREATED', 'SUBMITTED', 'TAKEN_IN_REVIEW', 'APPROVED', 'REJECTED',
    'CANCELLED', 'DOCUMENT_ATTACHED', 'DOCUMENT_VERIFIED',
    'DOCUMENT_REJECTED', 'COMMENTED', 'PERIOD_SET', 'MARKS_OVERRIDDEN',
]


class Migration(migrations.Migration):

    # Только своё предшествующее состояние — как и в `0004`.
    #
    # Автогенератор дописывает сюда `employees`, `organizations` и
    # пользователя по составу модели целиком, а не по нуждам операций.
    # Лишние рёбра пересортировывают общий граф, и `absences/0002`
    # уезжает за `core/0002`, который начинает вешать внешний ключ на
    # колонку, которой ещё нет.
    dependencies = [
        ('absences', '0004_sick_leave_without_dates'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='absenceaction',
            name='ck_absence_actions_action',
        ),
        migrations.AlterField(
            model_name='absenceaction',
            name='action',
            field=models.CharField(
                choices=[(one, one) for one in ACTIONS], max_length=30
            ),
        ),
        migrations.AddConstraint(
            model_name='absenceaction',
            constraint=models.CheckConstraint(
                condition=models.Q(('action__in', ACTIONS)),
                name='ck_absence_actions_action',
            ),
        ),
        migrations.AddConstraint(
            model_name='employeeabsence',
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                condition=models.Q(
                    ('status__in', ('PLANNED', 'ACTIVE', 'COMPLETED'))
                ),
                expressions=[
                    ('employee_id', '='),
                    (
                        models.Func(
                            models.F('start_at'),
                            models.F('end_at'),
                            models.Value('[]'),
                            function='tstzrange',
                            output_field=(
                                django.contrib.postgres.fields.ranges
                                .DateTimeRangeField()
                            ),
                        ),
                        '&&',
                    ),
                ],
                name='ex_employee_absences_overlap',
            ),
        ),
    ]
