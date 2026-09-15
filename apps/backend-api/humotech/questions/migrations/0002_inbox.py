"""Обращение становится перепиской.

Было: один вопрос, один ответ ассистента, один ответ кадровика и пять
состояний, из которых два («ответил ассистент», «ответил HR») говорили не
о том, что с обращением делать, а о том, кто последним писал.

Стало: состояния работы (NEW, IN_PROGRESS, WAITING_EMPLOYEE, CLOSED),
приоритет, категория, срок ответа и лента сообщений.

Перенос данных:

  * текст вопроса становится первым сообщением сотрудника;
  * ответ кадровика — сообщением HR в момент ответа;
  * ESCALATED_TO_HR — NEW или IN_PROGRESS, смотря назначен ли ответственный;
  * AI_ANSWERED и HR_ANSWERED — CLOSED с причиной: человеку уже ответили,
    и держать такие обращения открытыми значило бы вернуть их в очередь;
  * номера раздаются по дате создания внутри организации.
"""

import django.contrib.postgres.functions
import django.db.models.deletion
import django.db.models.expressions
import humotech.core.functions
from django.conf import settings
from django.db import migrations, models

STATUSES = ["NEW", "IN_PROGRESS", "WAITING_EMPLOYEE", "CLOSED"]
PRIORITIES = ["LOW", "NORMAL", "HIGH", "URGENT"]
CATEGORIES = [
    "VACATION", "SICK_LEAVE", "ATTENDANCE", "SCHEDULE", "SALARY",
    "DOCUMENTS", "TELEGRAM", "OTHER",
]
DRAFT_STATUSES = ["READY", "LOW_CONFIDENCE", "CONFLICT", "NO_SOURCES"]
KINDS = ["EMPLOYEE", "HR", "SYSTEM"]
SOURCES = ["TELEGRAM", "CRM", "SYSTEM"]
EVENTS = [
    "CREATED", "TAKEN", "ASSIGNED", "TRANSFERRED", "PRIORITY", "CATEGORY",
    "WAITING_EMPLOYEE", "RESUMED", "CLOSED", "REOPENED",
]


def _pairs(values):
    return [(value, value) for value in values]


def _raw(sql):
    return models.Q(
        django.db.models.expressions.RawSQL(
            sql, [], output_field=models.BooleanField()
        )
    )


def forward(apps, schema_editor):
    Question = apps.get_model("questions", "EmployeeQuestion")
    Message = apps.get_model("questions", "QuestionMessage")

    counters: dict = {}
    for row in Question.objects.order_by("organization_id", "created_at", "id"):
        number = counters.get(row.organization_id, 0) + 1
        counters[row.organization_id] = number
        row.number = number

        messages = [
            Message(
                organization_id=row.organization_id,
                question_id=row.id,
                kind="EMPLOYEE",
                source="TELEGRAM",
                body=row.question_text,
                author_employee_id=row.employee_id,
                created_at=row.created_at,
            )
        ]
        last = row.created_at
        answered = row.answered_at or row.updated_at

        if row.hr_answer_text:
            messages.append(
                Message(
                    organization_id=row.organization_id,
                    question_id=row.id,
                    kind="HR",
                    source="CRM",
                    body=row.hr_answer_text,
                    author_user_id=row.assigned_to_user_id,
                    created_at=answered,
                )
            )
            row.first_response_at = answered
            last = max(last, answered)

        old = row.status
        if old == "ESCALATED_TO_HR":
            row.status = "IN_PROGRESS" if row.assigned_to_user_id else "NEW"
            row.awaiting_reply = True
        elif old == "NEW":
            row.status = "NEW"
            row.awaiting_reply = True
        else:
            reason = {
                "AI_ANSWERED": "Ответ дал ассистент в Telegram",
                "HR_ANSWERED": "Ответ отправлен сотруднику",
            }.get(old)
            row.status = "CLOSED"
            row.closed_at = answered
            row.close_reason = reason
            row.awaiting_reply = False
            messages.append(
                Message(
                    organization_id=row.organization_id,
                    question_id=row.id,
                    kind="SYSTEM",
                    source="SYSTEM",
                    event="CLOSED",
                    details={"from": old, "to": "CLOSED", "reason": reason},
                    created_at=answered,
                )
            )
        if row.ai_answer_text:
            row.ai_status = "READY"
            row.ai_generated_at = row.created_at
        row.last_message_at = last
        row.save()
        Message.objects.bulk_create(messages)


def backward(apps, schema_editor):
    Question = apps.get_model("questions", "EmployeeQuestion")
    Message = apps.get_model("questions", "QuestionMessage")
    back = {
        "NEW": "NEW",
        "IN_PROGRESS": "ESCALATED_TO_HR",
        "WAITING_EMPLOYEE": "HR_ANSWERED",
        "CLOSED": "CLOSED",
    }
    for row in Question.objects.all():
        reply = (
            Message.objects.filter(question_id=row.id, kind="HR")
            .order_by("-created_at")
            .first()
        )
        row.status = back[row.status]
        row.hr_answer_text = reply.body if reply else None
        row.answered_at = reply.created_at if reply else None
        row.save()


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0005_photo_foreign_key"),
        ("knowledge", "0001_initial"),
        ("notifications", "0005_attempt_history_complete"),
        ("organizations", "0001_initial"),
        ("questions", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="employeequestion",
            name="ck_employee_questions_status",
        ),
        migrations.AlterField(
            model_name="employeequestion",
            name="status",
            field=models.CharField(choices=_pairs(STATUSES), max_length=30),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="number",
            field=models.IntegerField(default=0),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="priority",
            field=models.CharField(
                choices=_pairs(PRIORITIES), db_default="NORMAL", max_length=20
            ),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="category",
            field=models.CharField(
                choices=_pairs(CATEGORIES), db_default="OTHER", max_length=30
            ),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="channel",
            field=models.CharField(db_default="TELEGRAM", max_length=20),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="due_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="last_message_at",
            field=models.DateTimeField(
                db_default=humotech.core.functions.TransactionNow()
            ),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="unread",
            field=models.BooleanField(db_default=False),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="awaiting_reply",
            field=models.BooleanField(db_default=True),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="first_response_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="closed_by_user",
            field=models.ForeignKey(
                blank=True, db_column="closed_by_user_id", db_index=False,
                null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="close_reason",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="ai_status",
            field=models.CharField(
                blank=True, choices=_pairs(DRAFT_STATUSES), max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="ai_source_ids",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeequestion",
            name="ai_generated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="QuestionMessage",
            fields=[
                ("id", models.UUIDField(db_default=django.contrib.postgres.functions.RandomUUID(), editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_default=humotech.core.functions.TransactionNow(), editable=False)),
                ("kind", models.CharField(choices=_pairs(KINDS), max_length=20)),
                ("source", models.CharField(choices=_pairs(SOURCES), max_length=20)),
                ("body", models.TextField(blank=True, null=True)),
                ("event", models.CharField(blank=True, choices=_pairs(EVENTS), max_length=30, null=True)),
                ("details", models.JSONField(blank=True, null=True)),
                ("telegram_message_id", models.BigIntegerField(blank=True, null=True)),
                ("client_request_id", models.CharField(blank=True, max_length=100, null=True)),
                ("author_employee", models.ForeignKey(blank=True, db_column="author_employee_id", db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="employees.employee")),
                ("author_user", models.ForeignKey(blank=True, db_column="author_user_id", db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("notification", models.ForeignKey(blank=True, db_column="notification_id", db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="notifications.notification")),
                ("organization", models.ForeignKey(db_column="organization_id", db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="organizations.organization")),
                ("question", models.ForeignKey(db_column="question_id", db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="questions.employeequestion")),
            ],
            options={
                "verbose_name": "сообщение обращения",
                "verbose_name_plural": "сообщения обращений",
                "db_table": "employee_question_messages",
            },
        ),
        migrations.RunPython(forward, backward),
        migrations.RemoveField(model_name="employeequestion", name="hr_answer_text"),
        migrations.RemoveField(model_name="employeequestion", name="answered_at"),
        migrations.AddConstraint(
            model_name="employeequestion",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", STATUSES)),
                name="ck_employee_questions_status",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeequestion",
            constraint=models.CheckConstraint(
                condition=models.Q(("priority__in", PRIORITIES)),
                name="ck_employee_questions_priority",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeequestion",
            constraint=models.CheckConstraint(
                condition=models.Q(("category__in", CATEGORIES)),
                name="ck_employee_questions_category",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeequestion",
            constraint=models.CheckConstraint(
                condition=models.Q(("ai_status__isnull", True), ("ai_status__in", DRAFT_STATUSES), _connector="OR"),
                name="ck_employee_questions_ai_status",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeequestion",
            constraint=models.CheckConstraint(
                condition=_raw("status <> 'CLOSED' OR closed_at IS NOT NULL"),
                name="ck_employee_questions_closed_has_date",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeequestion",
            constraint=models.UniqueConstraint(
                fields=("organization", "number"),
                name="uq_employee_questions_number",
            ),
        ),
        migrations.AddIndex(
            model_name="employeequestion",
            index=models.Index(
                fields=["organization", "last_message_at"],
                name="ix_employee_questions_org_last",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionmessage",
            constraint=models.CheckConstraint(
                condition=models.Q(("kind__in", KINDS)),
                name="ck_question_messages_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionmessage",
            constraint=models.CheckConstraint(
                condition=models.Q(("source__in", SOURCES)),
                name="ck_question_messages_source",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionmessage",
            constraint=models.CheckConstraint(
                condition=models.Q(("event__isnull", True), ("event__in", EVENTS), _connector="OR"),
                name="ck_question_messages_event",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionmessage",
            constraint=models.CheckConstraint(
                condition=_raw("kind = 'SYSTEM' OR (body IS NOT NULL AND event IS NULL)"),
                name="ck_question_messages_body_or_event",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionmessage",
            constraint=models.UniqueConstraint(
                condition=models.Q(("client_request_id__isnull", False)),
                fields=("question", "client_request_id"),
                name="uq_question_messages_client_request",
            ),
        ),
        migrations.AddIndex(
            model_name="questionmessage",
            index=models.Index(
                fields=["organization"], name="ix_question_messages_organization_id"
            ),
        ),
        migrations.AddIndex(
            model_name="questionmessage",
            index=models.Index(
                fields=["question", "created_at"], name="ix_question_messages_question"
            ),
        ),
    ]
