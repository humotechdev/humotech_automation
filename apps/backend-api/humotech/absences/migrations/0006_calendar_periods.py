"""Календарные дни вместо моментов — и последний барьер по больничному.

**Пересечения.** Отпуск и больничный — это дни, а не отрезок времени.
Пока период сравнивался моментами, ответ на вопрос «заняты ли сутки»
зависел от того, в котором часу заканчивается последний день:
отсутствие до 18:00 и второе с 20:00 тех же суток формально не
пересекались. В табеле это один и тот же день.

Поэтому у отсутствия появляются `start_date` и `end_date`, а
ограничение сравнивает `daterange(start_date, end_date + 1, '[)')`.
Конец не входит в диапазон, и подгонять его микросекундами больше не
нужно. Моменты остаются: по ним считается табель и по ним же видно,
с какого часа человек отсутствовал.

Заполняются даты один раз, здесь, — переводом момента в пояс того
офиса, где человек числится. Пояс лежит в третьей таблице, поэтому
вычисляемой колонкой это не выразить: генерируемое выражение видит
только свою строку.

**Один незакрытый больничный.** У сотрудника не может быть двух
незакрытых заявок на отсутствие, требующее справки. Проверка в сервисе
остаётся — только она умеет назвать заявку, которую надо открыть. Но
заявку заводит не только личный кабинет: есть фоновые задачи, админка
и будущие endpoint'ы, и последнее слово должно быть за базой.

Частичным уникальным индексом это не выражается: условие «вид требует
справку» лежит в `absence_types`, а предикат индекса видит только свою
таблицу. Поэтому триггер — и он берёт ТУ ЖЕ советующую блокировку по
сотруднику, что и сервис. Без неё два одновременных запроса прочитали
бы друг друга до вставки и оба прошли бы.
"""

import django.contrib.postgres.constraints
import django.contrib.postgres.fields.ranges
from django.db import migrations, models
from django.db.models.expressions import RawSQL

OPEN_STATUSES = ("DRAFT", "SUBMITTED", "IN_REVIEW")

# Пояс берётся у офиса основного назначения, затем у организации, затем
# UTC. Назначение — последнее начавшееся: именно его показывает карточка
# сотрудника.
BACKFILL = """
UPDATE employee_absences a
   SET start_date = (a.start_at AT TIME ZONE z.tz)::date,
       end_date   = (a.end_at   AT TIME ZONE z.tz)::date
  FROM (
        SELECT ea.id,
               COALESCE(NULLIF(o.timezone, ''),
                        NULLIF(org.default_timezone, ''),
                        'UTC') AS tz
          FROM employee_absences ea
          JOIN organizations org ON org.id = ea.organization_id
          LEFT JOIN LATERAL (
                SELECT asg.office_id
                  FROM employee_assignments asg
                 WHERE asg.employee_id = ea.employee_id
                   AND asg.is_primary
                 ORDER BY asg.valid_from DESC
                 LIMIT 1
          ) prim ON TRUE
          LEFT JOIN offices o ON o.id = prim.office_id
  ) z
 WHERE z.id = a.id
"""

LOCK_FUNCTION = """
-- Номер блокировки отсутствий — в одном месте на всю систему.
--
-- Первое число говорит «про что» блокировка, второе — про кого. Её
-- берут и сервис, и триггер ниже, и правильность триггера держится
-- ровно на том, что номер один и тот же.
CREATE OR REPLACE FUNCTION absences_lock_employee(employee uuid)
RETURNS void AS $$
    SELECT pg_advisory_xact_lock(41001, hashtext(employee::text));
$$ LANGUAGE sql;
"""

TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION absences_one_open_sick_leave()
RETURNS trigger AS $$
BEGIN
    -- Правило только про исходную незакрытую заявку на то, к чему
    -- нужны бумаги. Продление, отмена и отпуск сюда не попадают.
    IF NEW.request_kind <> 'CREATE'
       OR NEW.status NOT IN ('DRAFT', 'SUBMITTED', 'IN_REVIEW') THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM absence_types t
         WHERE t.id = NEW.absence_type_id AND t.requires_document
    ) THEN
        RETURN NEW;
    END IF;

    -- Та же блокировка, что берёт сервис. Взятая здесь, она закрывает
    -- и те пути, которые о правиле не знают вовсе: пока её держит
    -- чужая транзакция, мы ждём, а дождавшись — видим её записи.
    PERFORM absences_lock_employee(NEW.employee_id);

    IF EXISTS (
        SELECT 1
          FROM absence_requests r
          JOIN absence_types t ON t.id = r.absence_type_id
         WHERE r.employee_id = NEW.employee_id
           AND r.id <> NEW.id
           AND r.request_kind = 'CREATE'
           AND r.status IN ('DRAFT', 'SUBMITTED', 'IN_REVIEW')
           AND t.requires_document
    ) THEN
        -- Имя ограничения проставляется явно: по нему общий разбор
        -- ошибок базы (`constraint_name_of`) узнаёт, что случилось,
        -- и сервис превращает это в понятный отказ. Без `CONSTRAINT`
        -- в диагностике пусто, и остаётся «нарушено ограничение
        -- целостности» — то есть ничего.
        RAISE EXCEPTION 'Больничный уже оформлен и ещё не закрыт'
            USING ERRCODE = 'unique_violation',
                  CONSTRAINT = 'uq_absence_requests_open_sick_leave',
                  TABLE = 'absence_requests';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER uq_absence_requests_open_sick_leave
    BEFORE INSERT OR UPDATE OF status, request_kind, absence_type_id, employee_id
    ON absence_requests
    FOR EACH ROW EXECUTE FUNCTION absences_one_open_sick_leave();
"""

DROP_TRIGGER = """
DROP TRIGGER IF EXISTS uq_absence_requests_open_sick_leave ON absence_requests;
DROP FUNCTION IF EXISTS absences_one_open_sick_leave();
DROP FUNCTION IF EXISTS absences_lock_employee(uuid);
"""


class Migration(migrations.Migration):

    # Только своё предшествующее состояние — как и в `0004`, `0005`.
    # Автогенератор дописывает сюда чужие приложения по составу модели, и
    # лишние рёбра пересортировывают общий граф.
    dependencies = [
        ('absences', '0005_overlap_and_marks'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeeabsence',
            name='start_date',
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name='employeeabsence',
            name='end_date',
            field=models.DateField(null=True),
        ),
        migrations.RunSQL(BACKFILL, migrations.RunSQL.noop),
        migrations.AlterField(
            model_name='employeeabsence',
            name='start_date',
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name='employeeabsence',
            name='end_date',
            field=models.DateField(),
        ),
        migrations.AddConstraint(
            model_name='employeeabsence',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    RawSQL(
                        'end_date >= start_date', [],
                        output_field=models.BooleanField(),
                    )
                ),
                name='ck_employee_absences_end_date_after_start_date',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='employeeabsence',
            name='ex_employee_absences_overlap',
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
                            models.F('start_date'),
                            RawSQL(
                                'end_date + 1', [],
                                output_field=models.DateField(),
                            ),
                            models.Value('[)'),
                            function='daterange',
                            output_field=(
                                django.contrib.postgres.fields.ranges
                                .DateRangeField()
                            ),
                        ),
                        '&&',
                    ),
                ],
                name='ex_employee_absences_overlap',
            ),
        ),
        migrations.RunSQL(LOCK_FUNCTION, DROP_TRIGGER),
        migrations.RunSQL(TRIGGER_FUNCTION, migrations.RunSQL.noop),
    ]
