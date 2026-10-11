"""Удаление функции «стажировка»: данные.

Выполняется ДО схемных изменений (0011): сначала перевести всех стажёров
в штат и сохранить снимок, потом снимать колонки и сужать CHECK — иначе
новое ограничение employment_status упало бы на существующих PROBATION.

Снимок кладётся во ВРЕМЕННУЮ таблицу `employees_probation_backup` (не
модель Django) ТОЛЬКО если есть что сохранять: на чистой установке
стажёров нет, лишняя пустая таблица не создаётся. Это НЕ часть постоянной
схемы, а страховка на время выката: удаляемые поля стажировки доступны,
пока не подтвердят, что удаление прошло успешно. Окончательное удаление —
отдельным шагом (`humotech/employees/sql/drop_probation_backup.sql`),
который выполняют на рабочей базе ПОСЛЕ проверок; тогда данных стажировки
не остаётся нигде. Бессрочно таблицу держать нельзя.

Сотрудники, должности, назначения, посещаемость, заявки и прочие кадровые
данные НЕ трогаются: меняется только employment_status PROBATION→ACTIVE и
делается копия трёх полей стажировки у тех, у кого они были.
"""

from django.db import migrations

FORWARD = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM employees
        WHERE employment_status = 'PROBATION'
           OR probation_from IS NOT NULL
           OR probation_to IS NOT NULL
           OR mentor_employee_id IS NOT NULL
    ) THEN
        CREATE TABLE IF NOT EXISTS employees_probation_backup (
            employee_id uuid PRIMARY KEY,
            employment_status varchar(20),
            probation_from date,
            probation_to date,
            mentor_employee_id uuid,
            backed_up_at timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO employees_probation_backup
            (employee_id, employment_status, probation_from, probation_to, mentor_employee_id)
        SELECT id, employment_status, probation_from, probation_to, mentor_employee_id
        FROM employees
        WHERE employment_status = 'PROBATION'
           OR probation_from IS NOT NULL
           OR probation_to IS NOT NULL
           OR mentor_employee_id IS NOT NULL
        ON CONFLICT (employee_id) DO NOTHING;
    END IF;
END $$;

UPDATE employees SET employment_status = 'ACTIVE' WHERE employment_status = 'PROBATION';
"""

# Откат возможен, только пока колонки ещё есть (т.е. при откате 0011 перед
# 0010): возвращаем из снимка всё, что снимали, — статус, даты стажировки
# и наставника. Откат 0011 воссоздаёт колонки пустыми, и без этого даты и
# наставник терялись бы, хотя лежат в снимке.
REVERSE = """
DO $$
BEGIN
    IF to_regclass('public.employees_probation_backup') IS NOT NULL THEN
        UPDATE employees e
        SET employment_status = CASE
                WHEN b.employment_status = 'PROBATION' THEN b.employment_status
                ELSE e.employment_status
            END,
            probation_from = b.probation_from,
            probation_to = b.probation_to,
            mentor_employee_id = b.mentor_employee_id
        FROM employees_probation_backup b
        WHERE e.id = b.employee_id;
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0009_mentor_key"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
