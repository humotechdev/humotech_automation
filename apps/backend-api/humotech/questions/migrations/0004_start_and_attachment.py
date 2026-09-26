"""«В работе» без передачи и файл к ответу HR.

  * событие STARTED: кадровик перевёл обращение в работу, не меняя
    ответственного;
  * `attachment_file_id` у сообщения: файл уходит сотруднику документом.
    Ключ — RESTRICT, как у остальных файлов: бумагу, отправленную
    человеку, из-под ленты не убрать.
"""

from django.db import migrations, models
import django.db.models.deletion

import humotech.core.enums

EVENTS = (
    "CREATED", "TAKEN", "ASSIGNED", "TRANSFERRED", "PRIORITY", "CATEGORY",
    "WAITING_EMPLOYEE", "RESUMED", "CLOSED", "REOPENED", "STARTED",
)

_FORWARD = """
DO $$
DECLARE existing text;
BEGIN
    SELECT c.conname INTO existing
      FROM pg_constraint c
     WHERE c.conrelid = 'employee_question_messages'::regclass
       AND c.contype = 'f'
       AND pg_get_constraintdef(c.oid) LIKE 'FOREIGN KEY (attachment_file_id)%';
    IF existing IS NOT NULL THEN
        EXECUTE format('ALTER TABLE employee_question_messages DROP CONSTRAINT %I', existing);
    END IF;
    ALTER TABLE employee_question_messages
      ADD CONSTRAINT fk_question_messages_attachment_file_id
      FOREIGN KEY (attachment_file_id) REFERENCES files (id) ON DELETE RESTRICT;
END $$;
"""

_BACKWARD = """
ALTER TABLE employee_question_messages
  DROP CONSTRAINT IF EXISTS fk_question_messages_attachment_file_id;
ALTER TABLE employee_question_messages
  ADD CONSTRAINT fk_question_messages_attachment_file_id
  FOREIGN KEY (attachment_file_id) REFERENCES files (id) DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("questions", "0003_foreign_keys"),
        ("files", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="questionmessage",
            name="event",
            field=models.CharField(
                blank=True,
                choices=[(one, one) for one in EVENTS],
                max_length=30,
                null=True,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="questionmessage",
            name="ck_question_messages_event",
        ),
        migrations.AddConstraint(
            model_name="questionmessage",
            constraint=humotech.core.enums.status_check(
                "event", EVENTS, "ck_question_messages_event", nullable=True,
            ),
        ),
        migrations.AddField(
            model_name="questionmessage",
            name="attachment",
            field=models.ForeignKey(
                blank=True,
                db_column="attachment_file_id",
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="files.file",
            ),
        ),
        migrations.RunSQL(_FORWARD, _BACKWARD),
    ]
