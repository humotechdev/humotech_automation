"""Внешние ключи переписки — с `ON DELETE`, как и все остальные.

Django пишет в DDL `DEFERRABLE INITIALLY DEFERRED` без `ON DELETE`, а
запрет физического удаления держится на действии в самой базе. Тот же
приём, что в `employees/0005`:

  * сообщение принадлежит обращению — CASCADE: без обращения ленты нет;
  * автор-сотрудник и организация — RESTRICT: сотрудника с перепиской
    удалить нельзя, его вопросы — часть кадровой истории;
  * автор-пользователь, закрывший и строка очереди — SET NULL: учётную
    запись HR можно убрать, а уведомление — вычистить, но сообщение
    и факт закрытия обязаны остаться.
"""

from django.db import migrations

# (таблица, имя ключа, колонки, целевая таблица, целевые колонки, ON DELETE)
FOREIGN_KEYS = [
    ('employee_questions', 'fk_employee_questions_closed_by_user_id',
     'closed_by_user_id', 'users', 'id', 'SET NULL'),
    ('employee_question_messages', 'fk_question_messages_question_id',
     'question_id', 'employee_questions', 'id', 'CASCADE'),
    ('employee_question_messages', 'fk_question_messages_organization_id',
     'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_question_messages', 'fk_question_messages_author_employee_id',
     'author_employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_question_messages', 'fk_question_messages_author_user_id',
     'author_user_id', 'users', 'id', 'SET NULL'),
    ('employee_question_messages', 'fk_question_messages_notification_id',
     'notification_id', 'notifications', 'id', 'SET NULL'),
]

_FORWARD = """
DO $$
DECLARE
    r record;
    existing text;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        %s
    ) AS t(tbl, name, cols, ref_tbl, ref_cols, action)
    LOOP
        -- Существующий ключ ищем по таблице и колонкам, а не по имени:
        -- имя ему дал Django и оно содержит хеш.
        SELECT c.conname INTO existing
          FROM pg_constraint c
         WHERE c.conrelid = r.tbl::regclass
           AND c.contype = 'f'
           AND pg_get_constraintdef(c.oid) LIKE 'FOREIGN KEY (' || r.cols || ')%%';

        IF existing IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %%I DROP CONSTRAINT %%I', r.tbl, existing);
        END IF;

        EXECUTE format(
            'ALTER TABLE %%I ADD CONSTRAINT %%I FOREIGN KEY (%%s) '
            'REFERENCES %%I (%%s) ON DELETE %%s',
            r.tbl, r.name, r.cols, r.ref_tbl, r.ref_cols, r.action);
    END LOOP;
END $$;
"""

_BACKWARD = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        %s
    ) AS t(tbl, name, cols, ref_tbl, ref_cols, action)
    LOOP
        EXECUTE format('ALTER TABLE %%I DROP CONSTRAINT IF EXISTS %%I', r.tbl, r.name);
        EXECUTE format(
            'ALTER TABLE %%I ADD CONSTRAINT %%I FOREIGN KEY (%%s) '
            'REFERENCES %%I (%%s) DEFERRABLE INITIALLY DEFERRED',
            r.tbl, r.name, r.cols, r.ref_tbl, r.ref_cols);
    END LOOP;
END $$;
"""


def _values_sql() -> str:
    return ",\n        ".join(
        "('{}', '{}', '{}', '{}', '{}', '{}')".format(*row)
        for row in FOREIGN_KEYS
    )


class Migration(migrations.Migration):

    dependencies = [
        ('questions', '0002_inbox'),
        ('core', '0002_raw_schema_objects'),
        ('employees', '0005_photo_foreign_key'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_FORWARD % _values_sql(),
            reverse_sql=_BACKWARD % _values_sql(),
        ),
    ]
