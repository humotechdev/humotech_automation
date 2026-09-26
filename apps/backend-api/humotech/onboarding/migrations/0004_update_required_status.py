"""Статус «Требуется ознакомление».

Выпуск новой редакции обязательного документа возвращает к
подтверждению того, кто программу уже прошёл. Прежде такой человек
оказывался в POLICIES_IN_PROGRESS — рядом с теми, кто просто не дошёл
до документов, — и кадровик не мог отличить одно от другого. Это
разные истории: первый своё сделал и ждёт нового текста, второй
застрял на середине.

Доступа статус не меняет: ознакомление бота не закрывает ни в каком
состоянии.
"""


from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0007_probation_period'),
        ('onboarding', '0003_seed_content'),
        ('organizations', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='employeeonboarding',
            name='ck_employee_onboarding_status',
        ),
        migrations.AlterField(
            model_name='employeeonboarding',
            name='status',
            field=models.CharField(choices=[('NOT_STARTED', 'NOT_STARTED'), ('IN_PROGRESS', 'IN_PROGRESS'), ('INFO_COMPLETED', 'INFO_COMPLETED'), ('POLICIES_IN_PROGRESS', 'POLICIES_IN_PROGRESS'), ('COMPLETED', 'COMPLETED'), ('UPDATE_REQUIRED', 'UPDATE_REQUIRED'), ('BLOCKED_BY_DECLINED_POLICY', 'BLOCKED_BY_DECLINED_POLICY')], max_length=30),
        ),
        migrations.AddConstraint(
            model_name='employeeonboarding',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['NOT_STARTED', 'IN_PROGRESS', 'INFO_COMPLETED', 'POLICIES_IN_PROGRESS', 'COMPLETED', 'UPDATE_REQUIRED', 'BLOCKED_BY_DECLINED_POLICY'])), name='ck_employee_onboarding_status'),
        ),
    ]
