"""Больничный без дат: отметка о заявлении и новое действие истории.

Даты у заявки уже были nullable — колонки менять не пришлось. Новое
здесь два:

  * `application_received_at` — подписанное заявление дошло по почте.
    Отдельно от справки: бумага подтверждает намерение человека,
    справка — факт болезни, и приходят они разными дорогами;
  * действие `PERIOD_SET` — кадровик проставил фактические даты по
    справке. По нему в истории видно, что период взят из документа, а
    не со слов заболевшего.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    # Только своё предшествующее состояние.
    #
    # Автогенератор дописал сюда `employees`, `organizations` и
    # пользователя — не потому, что они нужны этим операциям, а по
    # составу модели целиком. Лишние рёбра пересортировали общий граф:
    # `absences/0002` уехал ЗА `core/0002`, и тот начал вешать внешний
    # ключ на колонку, которой ещё нет. Правка полей существующих
    # таблиц чужих приложений не касается.
    dependencies = [
        ('absences', '0003_document_rejected_action'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='absenceaction',
            name='ck_absence_actions_action',
        ),
        migrations.AddField(
            model_name='absencerequest',
            name='application_received_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='absenceaction',
            name='action',
            field=models.CharField(choices=[('CREATED', 'CREATED'), ('SUBMITTED', 'SUBMITTED'), ('TAKEN_IN_REVIEW', 'TAKEN_IN_REVIEW'), ('APPROVED', 'APPROVED'), ('REJECTED', 'REJECTED'), ('CANCELLED', 'CANCELLED'), ('DOCUMENT_ATTACHED', 'DOCUMENT_ATTACHED'), ('DOCUMENT_VERIFIED', 'DOCUMENT_VERIFIED'), ('DOCUMENT_REJECTED', 'DOCUMENT_REJECTED'), ('COMMENTED', 'COMMENTED'), ('PERIOD_SET', 'PERIOD_SET')], max_length=30),
        ),
        migrations.AddConstraint(
            model_name='absenceaction',
            constraint=models.CheckConstraint(condition=models.Q(('action__in', ['CREATED', 'SUBMITTED', 'TAKEN_IN_REVIEW', 'APPROVED', 'REJECTED', 'CANCELLED', 'DOCUMENT_ATTACHED', 'DOCUMENT_VERIFIED', 'DOCUMENT_REJECTED', 'COMMENTED', 'PERIOD_SET'])), name='ck_absence_actions_action'),
        ),
    ]
