"""Удаление функции «стажировка»: данные.

Выполняется ДО схемных изменений (0011): сначала перевести всех стажёров
в штат и сохранить снимок, потом снимать колонки и сужать CHECK — иначе
новое ограничение employment_status упало бы на существующих PROBATION.

Снимок кладётся в отдельную таблицу `employees_probation_backup` (не
модель Django): удаляемые данные нигде не теряются, и при необходимости
их видно. На чистой установке стажёров нет — таблица создаётся пустой.

Сотрудники, должности, назначения, посещаемость, заявки и прочие кадровые
данные НЕ трогаются: меняется только employment_status PROBATION→ACTIVE и
делается копия трёх полей стажировки.
"""

from django.db import migrations

FORWARD = """
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

UPDATE employees SET employment_status = 'ACTIVE' WHERE employment_status = 'PROBATION';
"""

# Откат возможен, только пока колонки ещё есть (т.е. при откате 0011 перед
# 0010): возвращаем статус из снимка. Сами значения дат/наставника к этому
# моменту уже восстановит откат схемной миграции как пустые — снимок
# остаётся источником правды и читается вручную при необходимости.
REVERSE = """
UPDATE employees e
SET employment_status = b.employment_status
FROM employees_probation_backup b
WHERE e.id = b.employee_id AND b.employment_status = 'PROBATION';
"""


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0009_mentor_key"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
