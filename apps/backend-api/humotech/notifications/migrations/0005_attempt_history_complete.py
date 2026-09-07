"""Полнота истории попыток — постоянным признаком, а не выводом по данным.

До этой миграции «сохранена ли история» вычислялось на лету:
`попыток нет и счётчик нулевой и не отправлено`. Выражение опиралось на
`attempts`, а его обнуляет ручной повтор — поэтому у старой строки после
одного нажатия «Повторить» история объявлялась полной, хотя ранние
попытки не записывались никогда и появиться уже не могут.

Признак постоянный и ставится один раз. Обратное заполнение опирается
только на то, что можно установить достоверно:

  * есть хоть одна записанная попытка — история ведётся, полная;
  * строка отправлена или прочитана, а попыток не записано — отправка
    была, запись о ней потеряна: НЕПОЛНАЯ;
  * счётчик попыток ненулевой, а записей нет — то же самое: НЕПОЛНАЯ;
  * снята очередью (в `error_message` её код), а записей нет — снятие
    пишет попытку с исходом CANCELLED, значит запись потеряна: НЕПОЛНАЯ;
  * всё остальное — строка, у которой попыток ещё не было: полная.

Последний пункт включает снятые оператором (`cancelled_by_operator`):
ручное снятие попыткой доставки не является и записи не оставляет, так
что её отсутствие там ничего не теряет.

Спорные случаи разрешаются в пользу «полная» намеренно: неполнота — это
утверждение об утраченных данных, и объявлять её без основания значит
пугать разбирающегося там, где всё на месте.
"""

from django.db import migrations, models

BACKFILL = """
UPDATE notifications AS n
   SET attempt_history_complete = FALSE
 WHERE NOT EXISTS (
           SELECT 1 FROM notification_attempts a
            WHERE a.notification_id = n.id
       )
   AND (
           n.status IN ('SENT', 'READ')
        OR n.attempts > 0
        OR (
               n.status = 'CANCELLED'
           AND n.error_message IS NOT NULL
           AND n.error_message <> 'cancelled_by_operator'
           )
       );
"""

# Возврат честный: признак снимается целиком, а не «восстанавливается».
UNDO = "UPDATE notifications SET attempt_history_complete = TRUE;"


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0004_attempt_foreign_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='attempt_history_complete',
            field=models.BooleanField(db_default=True),
        ),
        migrations.RunSQL(sql=BACKFILL, reverse_sql=UNDO),
    ]
