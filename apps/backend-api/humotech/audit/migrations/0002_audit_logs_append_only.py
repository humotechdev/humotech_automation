"""Журнал аудита — только дополнение, и на уровне базы тоже.

Модель `AuditLog` запрещает `save()` существующей строки и `delete()`, но
это защищает лишь от добросовестного кода: `QuerySet.update()/delete()`
и прямой SQL модель обходят, а роль приложения `humotech_app` имеет
UPDATE и DELETE на все таблицы. При компрометации приложения журнал можно
было бы подчистить (наблюдение инвентаризации 11.10, §11.7).

Триггер:
  * UPDATE запрещён — кроме обнуления `actor_user_id` при удалении учётки
    (ON DELETE SET NULL), где остальная строка не меняется;
  * DELETE разрешён только в транзакции, где явно выставлено
    `SET LOCAL humotech.audit_purge = 'on'` — так поступают лишь сидеры
    изолированных стендов при пересоздании демо-данных
    (`humotech.audit.purge.allow_audit_purge`).
TRUNCATE у роли приложения нет; его выполняет только владелец схемы.
"""

from django.db import migrations

FORWARD = r"""
CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        -- Единственное допустимое изменение — ON DELETE SET NULL внешнего
        -- ключа на учётку: строка остаётся той же, теряется лишь ссылка
        -- на удалённого пользователя.
        IF OLD.actor_user_id IS NOT NULL AND NEW.actor_user_id IS NULL
           AND (to_jsonb(NEW) - 'actor_user_id') = (to_jsonb(OLD) - 'actor_user_id') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'audit_logs is append-only: UPDATE is forbidden'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF coalesce(current_setting('humotech.audit_purge', true), '') <> 'on' THEN
        RAISE EXCEPTION 'audit_logs is append-only: DELETE requires humotech.audit_purge'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs;
CREATE TRIGGER trg_audit_logs_append_only
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only();
"""

BACKWARD = r"""
DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs;
DROP FUNCTION IF EXISTS audit_logs_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
