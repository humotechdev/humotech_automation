# Снимок получателей и две новые оси аудитории.

from django.db import migrations, models

import humotech.core.enums


class Migration(migrations.Migration):

    dependencies = [
        ("surveys", "0004_automation_foreign_keys"),
    ]

    operations = [
        migrations.AddField(
            model_name="surveycampaign",
            name="audience_snapshot",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="surveycampaign",
            name="audience_kind",
            field=models.CharField(
                choices=[
                    ("EMPLOYEES", "EMPLOYEES"),
                    ("DEPARTMENT", "DEPARTMENT"),
                    ("OFFICE", "OFFICE"),
                    ("REGION", "REGION"),
                    ("POSITION", "POSITION"),
                    ("ALL", "ALL"),
                ],
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="surveycampaign",
            name="ck_survey_campaigns_audience_kind",
        ),
        migrations.AddConstraint(
            model_name="surveycampaign",
            constraint=humotech.core.enums.status_check(
                "audience_kind",
                ("EMPLOYEES", "DEPARTMENT", "OFFICE", "REGION", "POSITION", "ALL"),
                "ck_survey_campaigns_audience_kind",
            ),
        ),
    ]
