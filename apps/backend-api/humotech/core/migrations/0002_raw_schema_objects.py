"""Объекты схемы, которые Django не выражает средствами моделей.

Две группы.

**1. Внешние ключи.** Django задаёт `on_delete` на стороне Python: удаление
разбирает коллектор ORM, а в DDL никакого `ON DELETE` не попадает — вместо
этого добавляется `DEFERRABLE INITIALLY DEFERRED`. Для этой схемы так нельзя:
запрет физического удаления объектов с историей — требование, и держится оно
на `ON DELETE RESTRICT` в самой базе. Прямой `DELETE` мимо ORM обязан
отклоняться, а не тихо проходить.

Поэтому все 127 внешних ключей переобъявляются с исходным именем,
исходным действием при удалении и без отложенной проверки.
Действия: CASCADE: 6, RESTRICT: 107, SET NULL: 14.

Список сгенерирован из снимка эталонной схемы, а не переписан вручную:
ошибка в одном `ON DELETE` разрешила бы удаление истории и заметить это
было бы нечем.

**2. Полнотекстовые GIN-индексы.** `SearchVector` в Django оборачивает
колонки в `COALESCE(...)`, чего в эталоне нет. Это разница поведения на NULL,
а не записи, поэтому индексы создаются точным SQL. В состояние моделей они
не попадают — значит, `makemigrations --check` остаётся чистым.

Обе группы обратимы: откат возвращает внешние ключи в тот вид, который
строит сам Django, и убирает индексы.
"""

from django.db import migrations

# (таблица, имя ключа, колонки, целевая таблица, целевые колонки, ON DELETE)
FOREIGN_KEYS = [
    ('absence_actions', 'fk_absence_actions_absence_request_id', 'absence_request_id', 'absence_requests', 'id', 'RESTRICT'),
    ('absence_actions', 'fk_absence_actions_actor_employee_id', 'actor_employee_id', 'employees', 'id', 'RESTRICT'),
    ('absence_actions', 'fk_absence_actions_actor_user_id', 'actor_user_id', 'users', 'id', 'SET NULL'),
    ('absence_actions', 'fk_absence_actions_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('absence_documents', 'fk_absence_documents_absence_request_id', 'absence_request_id', 'absence_requests', 'id', 'RESTRICT'),
    ('absence_documents', 'fk_absence_documents_file_id', 'file_id', 'files', 'id', 'RESTRICT'),
    ('absence_documents', 'fk_absence_documents_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('absence_documents', 'fk_absence_documents_verified_by_user_id', 'verified_by_user_id', 'users', 'id', 'SET NULL'),
    ('absence_requests', 'fk_absence_requests_absence_type_id', 'absence_type_id', 'absence_types', 'id', 'RESTRICT'),
    ('absence_requests', 'fk_absence_requests_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('absence_requests', 'fk_absence_requests_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('absence_requests', 'fk_absence_requests_parent_request_id', 'parent_request_id', 'absence_requests', 'id', 'RESTRICT'),
    ('absence_requests', 'fk_absence_requests_reviewed_by_user_id', 'reviewed_by_user_id', 'users', 'id', 'SET NULL'),
    ('absence_types', 'fk_absence_types_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('answer_feedback', 'fk_answer_feedback_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('answer_feedback', 'fk_answer_feedback_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('answer_feedback', 'fk_answer_feedback_query_log_id', 'query_log_id', 'llm_query_logs', 'id', 'RESTRICT'),
    ('attendance_correction_requests', 'fk_attendance_correction_requests_attendance_session_id', 'attendance_session_id', 'attendance_sessions', 'id', 'RESTRICT'),
    ('attendance_correction_requests', 'fk_attendance_correction_requests_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('attendance_correction_requests', 'fk_attendance_correction_requests_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('attendance_correction_requests', 'fk_attendance_correction_requests_reviewed_by_user_id', 'reviewed_by_user_id', 'users', 'id', 'SET NULL'),
    ('attendance_events', 'fk_attendance_events_employee_device_id', 'employee_device_id', 'employee_devices', 'id', 'RESTRICT'),
    ('attendance_events', 'fk_attendance_events_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('attendance_events', 'fk_attendance_events_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('attendance_events', 'fk_attendance_events_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('attendance_events', 'fk_attendance_events_qr_display_session_id', 'qr_display_session_id', 'qr_display_sessions', 'id', 'RESTRICT'),
    ('attendance_events', 'fk_attendance_events_qr_point_id', 'qr_point_id', 'office_qr_points', 'id', 'RESTRICT'),
    ('attendance_sessions', 'fk_attendance_sessions_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('attendance_sessions', 'fk_attendance_sessions_entry_event_id', 'entry_event_id', 'attendance_events', 'id', 'RESTRICT'),
    ('attendance_sessions', 'fk_attendance_sessions_exit_event_id', 'exit_event_id', 'attendance_events', 'id', 'RESTRICT'),
    ('attendance_sessions', 'fk_attendance_sessions_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('attendance_sessions', 'fk_attendance_sessions_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('audit_logs', 'fk_audit_logs_actor_employee_id', 'actor_employee_id', 'employees', 'id', 'RESTRICT'),
    ('audit_logs', 'fk_audit_logs_actor_user_id', 'actor_user_id', 'users', 'id', 'SET NULL'),
    ('audit_logs', 'fk_audit_logs_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('calendar_exceptions', 'fk_calendar_exceptions_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('calendar_exceptions', 'fk_calendar_exceptions_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('departments', 'fk_departments_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('departments', 'fk_departments_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('departments', 'fk_departments_parent_department_id', 'parent_department_id', 'departments', 'id', 'RESTRICT'),
    ('employee_absences', 'fk_employee_absences_absence_type_id', 'absence_type_id', 'absence_types', 'id', 'RESTRICT'),
    ('employee_absences', 'fk_employee_absences_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_absences', 'fk_employee_absences_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_absences', 'fk_employee_absences_origin_request_id', 'origin_request_id', 'absence_requests', 'id', 'RESTRICT'),
    ('employee_assignments', 'fk_employee_assignments_department_id', 'department_id', 'departments', 'id', 'RESTRICT'),
    ('employee_assignments', 'fk_employee_assignments_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_assignments', 'fk_employee_assignments_manager_employee_id', 'manager_employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_assignments', 'fk_employee_assignments_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('employee_assignments', 'fk_employee_assignments_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_assignments', 'fk_employee_assignments_position_id', 'position_id', 'positions', 'id', 'RESTRICT'),
    ('employee_devices', 'fk_employee_devices_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_devices', 'fk_employee_devices_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_office_access', 'fk_employee_office_access_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_office_access', 'fk_employee_office_access_granted_by_user_id', 'granted_by_user_id', 'users', 'id', 'SET NULL'),
    ('employee_office_access', 'fk_employee_office_access_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('employee_office_access', 'fk_employee_office_access_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_questions', 'fk_employee_questions_answer_source_id', 'answer_source_id', 'knowledge_sources', 'id', 'RESTRICT'),
    ('employee_questions', 'fk_employee_questions_assigned_to_user_id', 'assigned_to_user_id', 'users', 'id', 'SET NULL'),
    ('employee_questions', 'fk_employee_questions_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_questions', 'fk_employee_questions_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_schedule_assignments', 'fk_employee_schedule_assignments_assigned_by_user_id', 'assigned_by_user_id', 'users', 'id', 'SET NULL'),
    ('employee_schedule_assignments', 'fk_employee_schedule_assignments_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('employee_schedule_assignments', 'fk_employee_schedule_assignments_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('employee_schedule_assignments', 'fk_employee_schedule_assignments_schedule_id', 'schedule_id', 'work_schedules', 'id', 'RESTRICT'),
    ('employees', 'fk_employees_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('faq_entries', 'fk_faq_entries_approved_by_user_id', 'approved_by_user_id', 'users', 'id', 'SET NULL'),
    ('faq_entries', 'fk_faq_entries_created_by_user_id', 'created_by_user_id', 'users', 'id', 'RESTRICT'),
    ('faq_entries', 'fk_faq_entries_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('faq_entries', 'fk_faq_entries_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('faq_entries', 'fk_faq_entries_region_id', 'region_id', 'regions', 'id', 'RESTRICT'),
    ('faq_entries', 'fk_faq_entries_source_id', 'source_id', 'knowledge_sources', 'id', 'RESTRICT'),
    ('files', 'fk_files_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('files', 'fk_files_uploaded_by_employee_id', 'uploaded_by_employee_id', 'employees', 'id', 'RESTRICT'),
    ('files', 'fk_files_uploaded_by_user_id', 'uploaded_by_user_id', 'users', 'id', 'SET NULL'),
    ('knowledge_chunks', 'fk_knowledge_chunks_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('knowledge_chunks', 'fk_knowledge_chunks_source_id', 'source_id', 'knowledge_sources', 'id', 'CASCADE'),
    ('knowledge_index_jobs', 'fk_knowledge_index_jobs_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('knowledge_index_jobs', 'fk_knowledge_index_jobs_source_id', 'source_id', 'knowledge_sources', 'id', 'CASCADE'),
    ('knowledge_sources', 'fk_knowledge_sources_approved_by_user_id', 'approved_by_user_id', 'users', 'id', 'SET NULL'),
    ('knowledge_sources', 'fk_knowledge_sources_created_by_user_id', 'created_by_user_id', 'users', 'id', 'RESTRICT'),
    ('knowledge_sources', 'fk_knowledge_sources_department_id', 'department_id', 'departments', 'id', 'RESTRICT'),
    ('knowledge_sources', 'fk_knowledge_sources_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('knowledge_sources', 'fk_knowledge_sources_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('knowledge_sources', 'fk_knowledge_sources_parent_source_id', 'parent_source_id', 'knowledge_sources', 'id', 'RESTRICT'),
    ('knowledge_sources', 'fk_knowledge_sources_region_id', 'region_id', 'regions', 'id', 'RESTRICT'),
    ('leave_balances', 'fk_leave_balances_absence_type_id', 'absence_type_id', 'absence_types', 'id', 'RESTRICT'),
    ('leave_balances', 'fk_leave_balances_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('leave_balances', 'fk_leave_balances_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('llm_query_logs', 'fk_llm_query_logs_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('llm_query_logs', 'fk_llm_query_logs_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('llm_query_logs', 'fk_llm_query_logs_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('llm_query_logs', 'fk_llm_query_logs_region_id', 'region_id', 'regions', 'id', 'RESTRICT'),
    ('notifications', 'fk_notifications_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('notifications', 'fk_notifications_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('office_networks', 'fk_office_networks_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('office_networks', 'fk_office_networks_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('office_qr_points', 'fk_office_qr_points_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('office_qr_points', 'fk_office_qr_points_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('offices', 'fk_offices_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('offices', 'fk_offices_region_id', 'region_id', 'regions', 'id', 'RESTRICT'),
    ('organization_settings', 'fk_organization_settings_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('positions', 'fk_positions_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('qr_display_sessions', 'fk_qr_display_sessions_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('qr_display_sessions', 'fk_qr_display_sessions_qr_point_id', 'qr_point_id', 'office_qr_points', 'id', 'RESTRICT'),
    ('qr_display_sessions', 'fk_qr_display_sessions_started_by_user_id', 'started_by_user_id', 'users', 'id', 'SET NULL'),
    ('regions', 'fk_regions_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('role_permissions', 'fk_role_permissions_permission_id', 'permission_id', 'permissions', 'id', 'CASCADE'),
    ('role_permissions', 'fk_role_permissions_role_id', 'role_id', 'roles', 'id', 'CASCADE'),
    ('roles', 'fk_roles_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('schedule_breaks', 'fk_schedule_breaks_schedule_day_id', 'schedule_day_id', 'schedule_days', 'id', 'CASCADE'),
    ('schedule_days', 'fk_schedule_days_schedule_id', 'schedule_id', 'work_schedules', 'id', 'CASCADE'),
    ('telegram_accounts', 'fk_telegram_accounts_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('telegram_accounts', 'fk_telegram_accounts_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('unanswered_questions', 'fk_unanswered_questions_assigned_to_user_id', 'assigned_to_user_id', 'users', 'id', 'SET NULL'),
    ('unanswered_questions', 'fk_unanswered_questions_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('unanswered_questions', 'fk_unanswered_questions_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('unanswered_questions', 'fk_unanswered_questions_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('unanswered_questions', 'fk_unanswered_questions_region_id', 'region_id', 'regions', 'id', 'RESTRICT'),
    ('unanswered_questions', 'fk_unanswered_questions_resolved_faq_id', 'resolved_faq_id', 'faq_entries', 'id', 'SET NULL'),
    ('user_role_scopes', 'fk_user_role_scopes_office_id', 'office_id', 'offices', 'id', 'RESTRICT'),
    ('user_role_scopes', 'fk_user_role_scopes_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('user_role_scopes', 'fk_user_role_scopes_region_id', 'region_id', 'regions', 'id', 'RESTRICT'),
    ('user_role_scopes', 'fk_user_role_scopes_role_id', 'role_id', 'roles', 'id', 'RESTRICT'),
    ('user_role_scopes', 'fk_user_role_scopes_user_id', 'user_id', 'users', 'id', 'RESTRICT'),
    ('users', 'fk_users_employee_id', 'employee_id', 'employees', 'id', 'RESTRICT'),
    ('users', 'fk_users_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
    ('work_schedules', 'fk_work_schedules_organization_id', 'organization_id', 'organizations', 'id', 'RESTRICT'),
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


FULLTEXT_INDEXES = [
    (
        "ix_knowledge_sources_fts",
        "CREATE INDEX ix_knowledge_sources_fts ON knowledge_sources "
        "USING gin (to_tsvector('simple', title || ' ' || content))",
    ),
    (
        "ix_knowledge_chunks_fts",
        "CREATE INDEX ix_knowledge_chunks_fts ON knowledge_chunks "
        "USING gin (to_tsvector('simple', chunk_text))",
    ),
]


class Migration(migrations.Migration):

    dependencies = [('core', '0001_extensions'), ('absences', '0001_initial'), ('accounts', '0001_initial'), ('ai_assistant', '0001_initial'), ('attendance', '0001_initial'), ('audit', '0001_initial'), ('departments', '0001_initial'), ('devices', '0001_initial'), ('employees', '0001_initial'), ('files', '0001_initial'), ('knowledge', '0001_initial'), ('notifications', '0001_initial'), ('offices', '0001_initial'), ('organizations', '0001_initial'), ('positions', '0001_initial'), ('qr_codes', '0001_initial'), ('questions', '0001_initial'), ('rbac', '0001_initial'), ('regions', '0001_initial'), ('schedules', '0001_initial'), ('telegram', '0001_initial')]

    operations = [
        migrations.RunSQL(
            sql=_FORWARD % _values_sql(),
            reverse_sql=_BACKWARD % _values_sql(),
        ),
        *[
            migrations.RunSQL(
                sql=create_sql,
                reverse_sql=f"DROP INDEX IF EXISTS {name}",
            )
            for name, create_sql in FULLTEXT_INDEXES
        ],
    ]
