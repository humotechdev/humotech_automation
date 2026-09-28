import django.contrib.postgres.functions
import django.db.models.deletion
import humotech.core.functions
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0004_day_notice_foreign_keys"),
        ("accounts", "0004_auth_rate_counters"),
        ("employees", "0006_termination_reason"),
        ("offices", "0001_initial"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FieldWorkRequest",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False,
                                         db_default=django.contrib.postgres.functions.RandomUUID())),
                ("created_at", models.DateTimeField(editable=False,
                                                      db_default=humotech.core.functions.TransactionNow())),
                ("updated_at", models.DateTimeField(editable=False,
                                                      db_default=humotech.core.functions.TransactionNow())),
                ("date", models.DateField()),
                ("status", models.CharField(max_length=20, choices=[(s, s) for s in (
                    "PENDING", "CONFIRMED", "DECLINED", "CANCELLED", "EXPIRED")])),
                ("scheduled_start", models.TimeField()),
                ("scheduled_end", models.TimeField()),
                ("norm_seconds", models.PositiveIntegerField()),
                ("requested_at", models.DateTimeField()),
                ("responded_at", models.DateTimeField(null=True, blank=True)),
                ("cancelled_at", models.DateTimeField(null=True, blank=True)),
                ("expires_at", models.DateTimeField()),
                ("work_location", models.CharField(max_length=255, blank=True, default="")),
                ("work_description", models.CharField(max_length=1000, blank=True, default="")),
                ("organization", models.ForeignKey(to="organizations.organization", on_delete=django.db.models.deletion.PROTECT,
                                                     db_column="organization_id", db_index=False, related_name="+")),
                ("employee", models.ForeignKey(to="employees.employee", on_delete=django.db.models.deletion.PROTECT,
                                                 db_column="employee_id", related_name="field_work_requests")),
                ("office", models.ForeignKey(to="offices.office", on_delete=django.db.models.deletion.PROTECT,
                                               db_column="office_id", related_name="field_work_requests")),
                ("requested_by_user", models.ForeignKey(to="accounts.user", on_delete=django.db.models.deletion.PROTECT,
                                                          db_column="requested_by_user_id", related_name="+")),
            ],
            options={
                "db_table": "attendance_field_work_requests",
                "constraints": [
                    models.CheckConstraint(condition=models.Q(status__in=[
                        "PENDING", "CONFIRMED", "DECLINED", "CANCELLED", "EXPIRED"]), name="ck_field_work_status"),
                    models.UniqueConstraint(fields=("employee", "date"),
                        condition=models.Q(status__in=("PENDING", "CONFIRMED")),
                        name="uq_field_work_active_day"),
                ],
                "indexes": [models.Index(fields=["organization", "date"], name="ix_field_work_org_date")],
            },
        ),
    ]
