"""initial HUMOTECH HR schema

Первая миграция: вся схема HR-системы целиком.

Порядок важен:
  1. btree_gist — расширение нужно ДО создания EXCLUDE-ограничений,
     которые запрещают пересечение назначений и графиков сотрудника;
  2. таблицы создаются в порядке зависимостей внешних ключей.

gen_random_uuid() входит в ядро PostgreSQL начиная с версии 13,
расширение pgcrypto для него больше не требуется.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # EXCLUDE ... USING gist по (uuid =, daterange &&) требует btree_gist
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table('organizations',
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('default_timezone', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')", name=op.f('ck_organizations_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations'))
    )
    op.create_index('uq_organizations_lower_code', 'organizations', [sa.literal_column('lower(code)')], unique=True)
    op.create_table('permissions',
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_permissions')),
    sa.UniqueConstraint('code', name=op.f('uq_permissions_code'))
    )
    op.create_table('absence_types',
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('is_paid', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('requires_approval', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('requires_document', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('document_required_after_days', sa.Integer(), nullable=True),
    sa.Column('deducts_leave_balance', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('document_required_after_days IS NULL OR document_required_after_days >= 0', name=op.f('ck_absence_types_document_days_non_negative')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_absence_types_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_absence_types')),
    sa.UniqueConstraint('organization_id', 'code', name='uq_absence_types_org_code')
    )
    op.create_index(op.f('ix_absence_types_organization_id'), 'absence_types', ['organization_id'], unique=False)
    op.create_table('employees',
    sa.Column('employee_number', sa.String(length=100), nullable=False),
    sa.Column('first_name', sa.String(length=100), nullable=False),
    sa.Column('last_name', sa.String(length=100), nullable=False),
    sa.Column('middle_name', sa.String(length=100), nullable=True),
    sa.Column('phone', sa.String(length=30), nullable=True),
    sa.Column('corporate_email', sa.String(length=255), nullable=True),
    sa.Column('personal_email', sa.String(length=255), nullable=True),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('hire_date', sa.Date(), nullable=False),
    sa.Column('termination_date', sa.Date(), nullable=True),
    sa.Column('preferred_language', sa.String(length=10), server_default='ru', nullable=False),
    sa.Column('employment_status', sa.String(length=30), nullable=False),
    sa.Column('telegram_connected', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("employment_status IN ('ACTIVE', 'PROBATION', 'SUSPENDED', 'TERMINATED', 'ARCHIVED')", name=op.f('ck_employees_employment_status')),
    sa.CheckConstraint('termination_date IS NULL OR termination_date >= hire_date', name=op.f('ck_employees_termination_after_hire')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_employees_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employees')),
    sa.UniqueConstraint('organization_id', 'employee_number', name='uq_employees_org_number')
    )
    op.create_index('ix_employees_org_status', 'employees', ['organization_id', 'employment_status'], unique=False)
    op.create_index(op.f('ix_employees_organization_id'), 'employees', ['organization_id'], unique=False)
    op.create_table('organization_settings',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_organization_settings_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organization_settings'))
    )
    op.create_index(op.f('ix_organization_settings_organization_id'), 'organization_settings', ['organization_id'], unique=False)
    op.create_index('uq_organization_settings_org_key', 'organization_settings', ['organization_id', 'key'], unique=True)
    op.create_table('positions',
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')", name=op.f('ck_positions_status')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_positions_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_positions')),
    sa.UniqueConstraint('organization_id', 'code', name='uq_positions_org_code')
    )
    op.create_index(op.f('ix_positions_organization_id'), 'positions', ['organization_id'], unique=False)
    op.create_table('regions',
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('timezone', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')", name=op.f('ck_regions_status')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_regions_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_regions')),
    sa.UniqueConstraint('organization_id', 'code', name='uq_regions_org_code')
    )
    op.create_index(op.f('ix_regions_organization_id'), 'regions', ['organization_id'], unique=False)
    op.create_table('roles',
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_system', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_roles_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_roles'))
    )
    op.create_index(op.f('ix_roles_organization_id'), 'roles', ['organization_id'], unique=False)
    op.create_index('uq_roles_org_code', 'roles', ['organization_id', 'code'], unique=True, postgresql_where=sa.text('organization_id IS NOT NULL'))
    op.create_index('uq_roles_system_code', 'roles', ['code'], unique=True, postgresql_where=sa.text('organization_id IS NULL'))
    op.create_table('work_schedules',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('timezone', sa.String(length=100), nullable=False),
    sa.Column('weekly_minutes', sa.Integer(), nullable=False),
    sa.Column('late_grace_minutes', sa.Integer(), server_default='0', nullable=False),
    sa.Column('early_leave_grace_minutes', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_flexible', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')", name=op.f('ck_work_schedules_status')),
    sa.CheckConstraint('early_leave_grace_minutes >= 0', name=op.f('ck_work_schedules_early_grace_non_negative')),
    sa.CheckConstraint('late_grace_minutes >= 0', name=op.f('ck_work_schedules_late_grace_non_negative')),
    sa.CheckConstraint('weekly_minutes > 0', name=op.f('ck_work_schedules_weekly_minutes_positive')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_work_schedules_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_work_schedules'))
    )
    op.create_index(op.f('ix_work_schedules_organization_id'), 'work_schedules', ['organization_id'], unique=False)
    op.create_table('employee_devices',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('device_identifier_hash', sa.Text(), nullable=False),
    sa.Column('platform', sa.String(length=30), nullable=True),
    sa.Column('device_name', sa.String(length=255), nullable=True),
    sa.Column('app_version', sa.String(length=50), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('trusted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('PENDING', 'TRUSTED', 'REVOKED')", name=op.f('ck_employee_devices_status')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_employee_devices_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_employee_devices_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employee_devices')),
    sa.UniqueConstraint('employee_id', 'device_identifier_hash', name='uq_employee_devices_hash')
    )
    op.create_index(op.f('ix_employee_devices_employee_id'), 'employee_devices', ['employee_id'], unique=False)
    op.create_index(op.f('ix_employee_devices_organization_id'), 'employee_devices', ['organization_id'], unique=False)
    op.create_table('leave_balances',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('absence_type_id', sa.UUID(), nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('allocated_minutes', sa.Integer(), server_default='0', nullable=False),
    sa.Column('reserved_minutes', sa.Integer(), server_default='0', nullable=False),
    sa.Column('used_minutes', sa.Integer(), server_default='0', nullable=False),
    sa.Column('adjustment_minutes', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('allocated_minutes >= 0', name=op.f('ck_leave_balances_allocated_non_negative')),
    sa.CheckConstraint('reserved_minutes >= 0', name=op.f('ck_leave_balances_reserved_non_negative')),
    sa.CheckConstraint('used_minutes >= 0', name=op.f('ck_leave_balances_used_non_negative')),
    sa.CheckConstraint('year BETWEEN 2000 AND 2200', name=op.f('ck_leave_balances_year_range')),
    sa.ForeignKeyConstraint(['absence_type_id'], ['absence_types.id'], name=op.f('fk_leave_balances_absence_type_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_leave_balances_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_leave_balances_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_balances')),
    sa.UniqueConstraint('employee_id', 'absence_type_id', 'year', name='uq_leave_balances_year')
    )
    op.create_index(op.f('ix_leave_balances_employee_id'), 'leave_balances', ['employee_id'], unique=False)
    op.create_index(op.f('ix_leave_balances_organization_id'), 'leave_balances', ['organization_id'], unique=False)
    op.create_table('notifications',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('notification_type', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('related_entity_type', sa.String(length=100), nullable=True),
    sa.Column('related_entity_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("channel IN ('TELEGRAM', 'EMAIL', 'PUSH', 'IN_APP')", name=op.f('ck_notifications_channel')),
    sa.CheckConstraint("status IN ('PENDING', 'SENT', 'FAILED', 'CANCELLED', 'READ')", name=op.f('ck_notifications_status')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_notifications_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_notifications_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    op.create_index('ix_notifications_employee_created', 'notifications', ['employee_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_notifications_organization_id'), 'notifications', ['organization_id'], unique=False)
    op.create_index('ix_notifications_pending', 'notifications', ['scheduled_at'], unique=False, postgresql_where=sa.text("status = 'PENDING'"))
    op.create_table('offices',
    sa.Column('region_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('timezone', sa.String(length=100), nullable=False),
    sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=True),
    sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=True),
    sa.Column('geofence_radius_m', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('opened_at', sa.Date(), nullable=True),
    sa.Column('closed_at', sa.Date(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'CLOSED', 'ARCHIVED')", name=op.f('ck_offices_status')),
    sa.CheckConstraint('closed_at IS NULL OR opened_at IS NULL OR closed_at >= opened_at', name=op.f('ck_offices_close_after_open')),
    sa.CheckConstraint('geofence_radius_m IS NULL OR geofence_radius_m > 0', name=op.f('ck_offices_geofence_positive')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_offices_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_offices_region_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_offices')),
    sa.UniqueConstraint('organization_id', 'code', name='uq_offices_org_code')
    )
    op.create_index(op.f('ix_offices_organization_id'), 'offices', ['organization_id'], unique=False)
    op.create_index(op.f('ix_offices_region_id'), 'offices', ['region_id'], unique=False)
    op.create_table('role_permissions',
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('permission_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], name=op.f('fk_role_permissions_permission_id'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_role_permissions_role_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_id', 'permission_id', name=op.f('pk_role_permissions'))
    )
    op.create_table('schedule_days',
    sa.Column('schedule_id', sa.UUID(), nullable=False),
    sa.Column('weekday', sa.SmallInteger(), nullable=False),
    sa.Column('is_working_day', sa.Boolean(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=True),
    sa.Column('end_time', sa.Time(), nullable=True),
    sa.Column('crosses_midnight', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('NOT is_working_day OR (start_time IS NOT NULL AND end_time IS NOT NULL)', name=op.f('ck_schedule_days_working_day_has_time')),
    sa.CheckConstraint('weekday BETWEEN 1 AND 7', name=op.f('ck_schedule_days_weekday_range')),
    sa.ForeignKeyConstraint(['schedule_id'], ['work_schedules.id'], name=op.f('fk_schedule_days_schedule_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schedule_days')),
    sa.UniqueConstraint('schedule_id', 'weekday', name='uq_schedule_days_weekday')
    )
    op.create_index(op.f('ix_schedule_days_schedule_id'), 'schedule_days', ['schedule_id'], unique=False)
    op.create_table('telegram_accounts',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
    sa.Column('telegram_chat_id', sa.BigInteger(), nullable=False),
    sa.Column('telegram_username', sa.String(length=255), nullable=True),
    sa.Column('language_code', sa.String(length=10), server_default='ru', nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_interaction_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('ACTIVE', 'REVOKED', 'BLOCKED')", name=op.f('ck_telegram_accounts_status')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_telegram_accounts_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_telegram_accounts_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_telegram_accounts')),
    sa.UniqueConstraint('employee_id', name='uq_telegram_accounts_employee'),
    sa.UniqueConstraint('telegram_user_id', name='uq_telegram_accounts_tg_user')
    )
    op.create_index(op.f('ix_telegram_accounts_organization_id'), 'telegram_accounts', ['organization_id'], unique=False)
    op.create_table('users',
    sa.Column('employee_id', sa.UUID(), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('mfa_enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('failed_login_attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'LOCKED', 'ARCHIVED')", name=op.f('ck_users_status')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_users_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_users_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index(op.f('ix_users_organization_id'), 'users', ['organization_id'], unique=False)
    op.create_index('uq_users_employee_id', 'users', ['employee_id'], unique=True, postgresql_where=sa.text('employee_id IS NOT NULL'))
    op.create_index('uq_users_org_lower_email', 'users', ['organization_id', sa.literal_column('lower(email)')], unique=True)
    op.create_table('absence_requests',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('absence_type_id', sa.UUID(), nullable=False),
    sa.Column('parent_request_id', sa.UUID(), nullable=True),
    sa.Column('request_kind', sa.String(length=20), nullable=False),
    sa.Column('requested_start_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requested_end_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('employee_comment', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reviewed_by_user_id', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_comment', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("request_kind = 'CREATE' OR parent_request_id IS NOT NULL", name=op.f('ck_absence_requests_derived_needs_parent')),
    sa.CheckConstraint("request_kind IN ('CREATE', 'EXTEND', 'CANCEL')", name=op.f('ck_absence_requests_request_kind')),
    sa.CheckConstraint("status IN ('DRAFT', 'SUBMITTED', 'IN_REVIEW', 'APPROVED', 'REJECTED', 'CANCELLED')", name=op.f('ck_absence_requests_status')),
    sa.CheckConstraint('parent_request_id IS NULL OR parent_request_id <> id', name=op.f('ck_absence_requests_no_self_parent')),
    sa.CheckConstraint('requested_end_at IS NULL OR requested_start_at IS NULL OR requested_end_at >= requested_start_at', name=op.f('ck_absence_requests_end_after_start')),
    sa.ForeignKeyConstraint(['absence_type_id'], ['absence_types.id'], name=op.f('fk_absence_requests_absence_type_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_absence_requests_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_absence_requests_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_request_id'], ['absence_requests.id'], name=op.f('fk_absence_requests_parent_request_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reviewed_by_user_id'], ['users.id'], name=op.f('fk_absence_requests_reviewed_by_user_id'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_absence_requests'))
    )
    op.create_index(op.f('ix_absence_requests_employee_id'), 'absence_requests', ['employee_id'], unique=False)
    op.create_index(op.f('ix_absence_requests_organization_id'), 'absence_requests', ['organization_id'], unique=False)
    op.create_index('ix_absence_requests_status', 'absence_requests', ['organization_id', 'status'], unique=False)
    op.create_table('audit_logs',
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('actor_employee_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('entity_type', sa.String(length=100), nullable=False),
    sa.Column('entity_id', sa.UUID(), nullable=False),
    sa.Column('old_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('new_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['actor_employee_id'], ['employees.id'], name=op.f('fk_audit_logs_actor_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_audit_logs_actor_user_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_audit_logs_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index('ix_audit_logs_actor_user', 'audit_logs', ['actor_user_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('ix_audit_logs_entity', 'audit_logs', ['entity_type', 'entity_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('ix_audit_logs_org_time', 'audit_logs', ['organization_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index(op.f('ix_audit_logs_organization_id'), 'audit_logs', ['organization_id'], unique=False)
    op.create_table('calendar_exceptions',
    sa.Column('office_id', sa.UUID(), nullable=True),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('exception_type', sa.String(length=30), nullable=False),
    sa.Column('is_working_day', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("exception_type IN ('HOLIDAY', 'SHORT_DAY', 'WORKING_WEEKEND', 'CLOSURE')", name=op.f('ck_calendar_exceptions_exception_type')),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_calendar_exceptions_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_calendar_exceptions_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calendar_exceptions'))
    )
    op.create_index(op.f('ix_calendar_exceptions_office_id'), 'calendar_exceptions', ['office_id'], unique=False)
    op.create_index(op.f('ix_calendar_exceptions_organization_id'), 'calendar_exceptions', ['organization_id'], unique=False)
    op.create_index('uq_calendar_exceptions_office_date', 'calendar_exceptions', ['office_id', 'date'], unique=True, postgresql_where=sa.text('office_id IS NOT NULL'))
    op.create_index('uq_calendar_exceptions_org_date', 'calendar_exceptions', ['organization_id', 'date'], unique=True, postgresql_where=sa.text('office_id IS NULL'))
    op.create_table('departments',
    sa.Column('office_id', sa.UUID(), nullable=False),
    sa.Column('parent_department_id', sa.UUID(), nullable=True),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')", name=op.f('ck_departments_status')),
    sa.CheckConstraint('parent_department_id IS NULL OR parent_department_id <> id', name=op.f('ck_departments_no_self_parent')),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_departments_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_departments_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_department_id'], ['departments.id'], name=op.f('fk_departments_parent_department_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_departments')),
    sa.UniqueConstraint('office_id', 'code', name='uq_departments_office_code')
    )
    op.create_index(op.f('ix_departments_office_id'), 'departments', ['office_id'], unique=False)
    op.create_index(op.f('ix_departments_organization_id'), 'departments', ['organization_id'], unique=False)
    op.create_index(op.f('ix_departments_parent_department_id'), 'departments', ['parent_department_id'], unique=False)
    op.create_table('employee_office_access',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('office_id', sa.UUID(), nullable=False),
    sa.Column('access_type', sa.String(length=30), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('granted_by_user_id', sa.UUID(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("access_type IN ('PRIMARY', 'TEMPORARY', 'PERMANENT', 'VISITOR')", name=op.f('ck_employee_office_access_access_type')),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name=op.f('ck_employee_office_access_valid_period')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_employee_office_access_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['granted_by_user_id'], ['users.id'], name=op.f('fk_employee_office_access_granted_by_user_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_employee_office_access_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_employee_office_access_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employee_office_access')),
    sa.UniqueConstraint('employee_id', 'office_id', 'valid_from', name='uq_employee_office_access_period')
    )
    op.create_index(op.f('ix_employee_office_access_employee_id'), 'employee_office_access', ['employee_id'], unique=False)
    op.create_index(op.f('ix_employee_office_access_office_id'), 'employee_office_access', ['office_id'], unique=False)
    op.create_index(op.f('ix_employee_office_access_organization_id'), 'employee_office_access', ['organization_id'], unique=False)
    op.create_table('employee_schedule_assignments',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('schedule_id', sa.UUID(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('assigned_by_user_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    postgresql.ExcludeConstraint((sa.column('employee_id'), '='), (sa.literal_column("daterange(valid_from, valid_to, '[]')"), '&&'), using='gist', name='ex_employee_schedule_assignments_overlap'),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name=op.f('ck_employee_schedule_assignments_valid_period')),
    sa.ForeignKeyConstraint(['assigned_by_user_id'], ['users.id'], name=op.f('fk_employee_schedule_assignments_assigned_by_user_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_employee_schedule_assignments_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_employee_schedule_assignments_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['schedule_id'], ['work_schedules.id'], name=op.f('fk_employee_schedule_assignments_schedule_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employee_schedule_assignments'))
    )
    op.create_index(op.f('ix_employee_schedule_assignments_employee_id'), 'employee_schedule_assignments', ['employee_id'], unique=False)
    op.create_index(op.f('ix_employee_schedule_assignments_organization_id'), 'employee_schedule_assignments', ['organization_id'], unique=False)
    op.create_index(op.f('ix_employee_schedule_assignments_schedule_id'), 'employee_schedule_assignments', ['schedule_id'], unique=False)
    op.create_table('files',
    sa.Column('storage_provider', sa.String(length=50), nullable=False),
    sa.Column('storage_key', sa.Text(), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('mime_type', sa.String(length=100), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('checksum_sha256', sa.String(length=64), nullable=False),
    sa.Column('uploaded_by_employee_id', sa.UUID(), nullable=True),
    sa.Column('uploaded_by_user_id', sa.UUID(), nullable=True),
    sa.Column('scan_status', sa.String(length=20), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("scan_status IN ('PENDING', 'CLEAN', 'INFECTED', 'FAILED')", name=op.f('ck_files_scan_status')),
    sa.CheckConstraint('char_length(checksum_sha256) = 64', name=op.f('ck_files_checksum_length')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_files_size_non_negative')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_files_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploaded_by_employee_id'], ['employees.id'], name=op.f('fk_files_uploaded_by_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploaded_by_user_id'], ['users.id'], name=op.f('fk_files_uploaded_by_user_id'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_files'))
    )
    op.create_index(op.f('ix_files_organization_id'), 'files', ['organization_id'], unique=False)
    op.create_index('uq_files_storage_key', 'files', ['storage_provider', 'storage_key'], unique=True)
    op.create_table('knowledge_articles',
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=False),
    sa.Column('approved_by_user_id', sa.UUID(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('DRAFT', 'IN_REVIEW', 'PUBLISHED', 'ARCHIVED')", name=op.f('ck_knowledge_articles_status')),
    sa.CheckConstraint('version > 0', name=op.f('ck_knowledge_articles_version_positive')),
    sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], name=op.f('fk_knowledge_articles_approved_by_user_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_knowledge_articles_created_by_user_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_knowledge_articles_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_articles'))
    )
    op.create_index('ix_knowledge_articles_org_status', 'knowledge_articles', ['organization_id', 'status'], unique=False)
    op.create_index(op.f('ix_knowledge_articles_organization_id'), 'knowledge_articles', ['organization_id'], unique=False)
    op.create_table('office_networks',
    sa.Column('office_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('network_cidr', postgresql.CIDR(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_office_networks_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_office_networks_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_office_networks')),
    sa.UniqueConstraint('office_id', 'network_cidr', name='uq_office_networks_cidr')
    )
    op.create_index(op.f('ix_office_networks_office_id'), 'office_networks', ['office_id'], unique=False)
    op.create_index(op.f('ix_office_networks_organization_id'), 'office_networks', ['organization_id'], unique=False)
    op.create_table('office_qr_points',
    sa.Column('office_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('direction_mode', sa.String(length=20), nullable=False),
    sa.Column('qr_mode', sa.String(length=20), server_default='ROTATING', nullable=False),
    sa.Column('static_token_hash', sa.Text(), nullable=True),
    sa.Column('rotation_seconds', sa.Integer(), nullable=True),
    sa.Column('token_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('require_geolocation', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('require_office_network', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('allowed_location_accuracy_m', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(qr_mode = 'STATIC'   AND static_token_hash IS NOT NULL) OR (qr_mode = 'ROTATING' AND rotation_seconds IS NOT NULL)", name=op.f('ck_office_qr_points_mode_requires_fields')),
    sa.CheckConstraint("direction_mode IN ('ENTRY', 'EXIT', 'BOTH')", name=op.f('ck_office_qr_points_direction_mode')),
    sa.CheckConstraint("qr_mode IN ('STATIC', 'ROTATING')", name=op.f('ck_office_qr_points_qr_mode')),
    sa.CheckConstraint('allowed_location_accuracy_m IS NULL OR allowed_location_accuracy_m > 0', name=op.f('ck_office_qr_points_accuracy_positive')),
    sa.CheckConstraint('rotation_seconds IS NULL OR rotation_seconds BETWEEN 15 AND 300', name=op.f('ck_office_qr_points_rotation_seconds_range')),
    sa.CheckConstraint('token_version > 0', name=op.f('ck_office_qr_points_token_version_positive')),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_office_qr_points_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_office_qr_points_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_office_qr_points')),
    sa.UniqueConstraint('office_id', 'code', name='uq_office_qr_points_office_code')
    )
    op.create_index(op.f('ix_office_qr_points_office_id'), 'office_qr_points', ['office_id'], unique=False)
    op.create_index(op.f('ix_office_qr_points_organization_id'), 'office_qr_points', ['organization_id'], unique=False)
    op.create_table('schedule_breaks',
    sa.Column('schedule_day_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('is_paid', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['schedule_day_id'], ['schedule_days.id'], name=op.f('fk_schedule_breaks_schedule_day_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schedule_breaks'))
    )
    op.create_index(op.f('ix_schedule_breaks_schedule_day_id'), 'schedule_breaks', ['schedule_day_id'], unique=False)
    op.create_table('user_role_scopes',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('region_id', sa.UUID(), nullable=True),
    sa.Column('office_id', sa.UUID(), nullable=True),
    sa.Column('valid_from', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('region_id IS NULL OR office_id IS NULL', name=op.f('ck_user_role_scopes_scope_not_both')),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name=op.f('ck_user_role_scopes_valid_period')),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_user_role_scopes_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_user_role_scopes_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_user_role_scopes_region_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_user_role_scopes_role_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_role_scopes_user_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_role_scopes')),
    sa.UniqueConstraint('user_id', 'role_id', 'region_id', 'office_id', 'valid_from', name='uq_user_role_scopes_grant')
    )
    op.create_index(op.f('ix_user_role_scopes_organization_id'), 'user_role_scopes', ['organization_id'], unique=False)
    op.create_index(op.f('ix_user_role_scopes_role_id'), 'user_role_scopes', ['role_id'], unique=False)
    op.create_index(op.f('ix_user_role_scopes_user_id'), 'user_role_scopes', ['user_id'], unique=False)
    op.create_table('absence_actions',
    sa.Column('absence_request_id', sa.UUID(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('actor_employee_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=30), nullable=False),
    sa.Column('previous_status', sa.String(length=20), nullable=True),
    sa.Column('new_status', sa.String(length=20), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action IN ('CREATED', 'SUBMITTED', 'TAKEN_IN_REVIEW', 'APPROVED', 'REJECTED', 'CANCELLED', 'DOCUMENT_ATTACHED', 'DOCUMENT_VERIFIED', 'COMMENTED')", name=op.f('ck_absence_actions_action')),
    sa.ForeignKeyConstraint(['absence_request_id'], ['absence_requests.id'], name=op.f('fk_absence_actions_absence_request_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['actor_employee_id'], ['employees.id'], name=op.f('fk_absence_actions_actor_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_absence_actions_actor_user_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_absence_actions_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_absence_actions'))
    )
    op.create_index(op.f('ix_absence_actions_absence_request_id'), 'absence_actions', ['absence_request_id'], unique=False)
    op.create_index(op.f('ix_absence_actions_organization_id'), 'absence_actions', ['organization_id'], unique=False)
    op.create_table('absence_documents',
    sa.Column('absence_request_id', sa.UUID(), nullable=False),
    sa.Column('file_id', sa.UUID(), nullable=False),
    sa.Column('document_type', sa.String(length=50), nullable=False),
    sa.Column('verification_status', sa.String(length=20), nullable=False),
    sa.Column('verified_by_user_id', sa.UUID(), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verification_comment', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("verification_status IN ('PENDING', 'VERIFIED', 'REJECTED')", name=op.f('ck_absence_documents_verification_status')),
    sa.ForeignKeyConstraint(['absence_request_id'], ['absence_requests.id'], name=op.f('fk_absence_documents_absence_request_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], name=op.f('fk_absence_documents_file_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_absence_documents_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['verified_by_user_id'], ['users.id'], name=op.f('fk_absence_documents_verified_by_user_id'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_absence_documents')),
    sa.UniqueConstraint('absence_request_id', 'file_id', name='uq_absence_documents_file')
    )
    op.create_index(op.f('ix_absence_documents_absence_request_id'), 'absence_documents', ['absence_request_id'], unique=False)
    op.create_index(op.f('ix_absence_documents_organization_id'), 'absence_documents', ['organization_id'], unique=False)
    op.create_table('employee_absences',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('absence_type_id', sa.UUID(), nullable=False),
    sa.Column('origin_request_id', sa.UUID(), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('PLANNED', 'ACTIVE', 'COMPLETED', 'CANCELLED')", name=op.f('ck_employee_absences_status')),
    sa.CheckConstraint('end_at >= start_at', name=op.f('ck_employee_absences_end_after_start')),
    sa.ForeignKeyConstraint(['absence_type_id'], ['absence_types.id'], name=op.f('fk_employee_absences_absence_type_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_employee_absences_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_employee_absences_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['origin_request_id'], ['absence_requests.id'], name=op.f('fk_employee_absences_origin_request_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employee_absences'))
    )
    op.create_index(op.f('ix_employee_absences_employee_id'), 'employee_absences', ['employee_id'], unique=False)
    op.create_index(op.f('ix_employee_absences_organization_id'), 'employee_absences', ['organization_id'], unique=False)
    op.create_index('ix_employee_absences_period', 'employee_absences', ['employee_id', 'start_at', 'end_at'], unique=False)
    op.create_table('employee_assignments',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('office_id', sa.UUID(), nullable=False),
    sa.Column('department_id', sa.UUID(), nullable=True),
    sa.Column('position_id', sa.UUID(), nullable=True),
    sa.Column('manager_employee_id', sa.UUID(), nullable=True),
    sa.Column('employment_type', sa.String(length=30), nullable=False),
    sa.Column('work_mode', sa.String(length=30), nullable=False),
    sa.Column('is_primary', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    postgresql.ExcludeConstraint((sa.column('employee_id'), '='), (sa.literal_column("daterange(valid_from, valid_to, '[]')"), '&&'), where=sa.text('is_primary'), using='gist', name='ex_employee_assignments_primary_overlap'),
    sa.CheckConstraint("employment_type IN ('FULL_TIME', 'PART_TIME', 'CONTRACT', 'INTERN')", name=op.f('ck_employee_assignments_employment_type')),
    sa.CheckConstraint("work_mode IN ('ONSITE', 'HYBRID', 'REMOTE')", name=op.f('ck_employee_assignments_work_mode')),
    sa.CheckConstraint('manager_employee_id IS NULL OR manager_employee_id <> employee_id', name=op.f('ck_employee_assignments_no_self_manager')),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name=op.f('ck_employee_assignments_valid_period')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_employee_assignments_department_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_employee_assignments_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['manager_employee_id'], ['employees.id'], name=op.f('fk_employee_assignments_manager_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_employee_assignments_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_employee_assignments_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['position_id'], ['positions.id'], name=op.f('fk_employee_assignments_position_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employee_assignments'))
    )
    op.create_index(op.f('ix_employee_assignments_employee_id'), 'employee_assignments', ['employee_id'], unique=False)
    op.create_index(op.f('ix_employee_assignments_office_id'), 'employee_assignments', ['office_id'], unique=False)
    op.create_index(op.f('ix_employee_assignments_organization_id'), 'employee_assignments', ['organization_id'], unique=False)
    op.create_table('employee_questions',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('question_text', sa.Text(), nullable=False),
    sa.Column('normalized_topic', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('ai_answer_text', sa.Text(), nullable=True),
    sa.Column('ai_confidence', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('answer_source_article_id', sa.UUID(), nullable=True),
    sa.Column('assigned_to_user_id', sa.UUID(), nullable=True),
    sa.Column('hr_answer_text', sa.Text(), nullable=True),
    sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('NEW', 'AI_ANSWERED', 'ESCALATED_TO_HR', 'HR_ANSWERED', 'CLOSED')", name=op.f('ck_employee_questions_status')),
    sa.CheckConstraint('ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1)', name=op.f('ck_employee_questions_confidence_range')),
    sa.ForeignKeyConstraint(['answer_source_article_id'], ['knowledge_articles.id'], name=op.f('fk_employee_questions_answer_source_article_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['assigned_to_user_id'], ['users.id'], name=op.f('fk_employee_questions_assigned_to_user_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_employee_questions_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_employee_questions_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employee_questions'))
    )
    op.create_index(op.f('ix_employee_questions_employee_id'), 'employee_questions', ['employee_id'], unique=False)
    op.create_index('ix_employee_questions_org_status', 'employee_questions', ['organization_id', 'status'], unique=False)
    op.create_index(op.f('ix_employee_questions_organization_id'), 'employee_questions', ['organization_id'], unique=False)
    op.create_table('qr_display_sessions',
    sa.Column('qr_point_id', sa.UUID(), nullable=False),
    sa.Column('started_by_user_id', sa.UUID(), nullable=True),
    sa.Column('display_identifier_hash', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('ACTIVE', 'EXPIRED', 'REVOKED', 'CLOSED')", name=op.f('ck_qr_display_sessions_status')),
    sa.CheckConstraint('ended_at IS NULL OR ended_at >= started_at', name=op.f('ck_qr_display_sessions_end_after_start')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_qr_display_sessions_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['qr_point_id'], ['office_qr_points.id'], name=op.f('fk_qr_display_sessions_qr_point_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['started_by_user_id'], ['users.id'], name=op.f('fk_qr_display_sessions_started_by_user_id'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_qr_display_sessions'))
    )
    op.create_index(op.f('ix_qr_display_sessions_organization_id'), 'qr_display_sessions', ['organization_id'], unique=False)
    op.create_index(op.f('ix_qr_display_sessions_qr_point_id'), 'qr_display_sessions', ['qr_point_id'], unique=False)
    op.create_table('attendance_events',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('office_id', sa.UUID(), nullable=False),
    sa.Column('qr_point_id', sa.UUID(), nullable=True),
    sa.Column('qr_display_session_id', sa.UUID(), nullable=True),
    sa.Column('employee_device_id', sa.UUID(), nullable=True),
    sa.Column('event_type', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('verification_status', sa.String(length=20), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('qr_nonce_hash', sa.Text(), nullable=True),
    sa.Column('qr_issued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('qr_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=True),
    sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=True),
    sa.Column('location_accuracy_m', sa.Numeric(precision=8, scale=2), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('inside_geofence', sa.Boolean(), nullable=True),
    sa.Column('inside_office_network', sa.Boolean(), nullable=True),
    sa.Column('client_event_id', sa.String(length=255), nullable=True),
    sa.Column('rejection_reason', sa.String(length=255), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("event_type IN ('ENTRY', 'EXIT')", name=op.f('ck_attendance_events_event_type')),
    sa.CheckConstraint("source <> 'QR' OR qr_point_id IS NOT NULL", name=op.f('ck_attendance_events_qr_requires_point')),
    sa.CheckConstraint("source IN ('QR', 'MANUAL', 'IMPORT')", name=op.f('ck_attendance_events_source')),
    sa.CheckConstraint("verification_status IN ('ACCEPTED', 'REJECTED', 'REVIEW')", name=op.f('ck_attendance_events_verification_status')),
    sa.CheckConstraint('qr_expires_at IS NULL OR qr_issued_at IS NULL OR qr_expires_at > qr_issued_at', name=op.f('ck_attendance_events_qr_expiry_after_issue')),
    sa.ForeignKeyConstraint(['employee_device_id'], ['employee_devices.id'], name=op.f('fk_attendance_events_employee_device_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_attendance_events_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_attendance_events_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_attendance_events_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['qr_display_session_id'], ['qr_display_sessions.id'], name=op.f('fk_attendance_events_qr_display_session_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['qr_point_id'], ['office_qr_points.id'], name=op.f('fk_attendance_events_qr_point_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attendance_events'))
    )
    op.create_index('ix_attendance_events_employee_time', 'attendance_events', ['employee_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('ix_attendance_events_office_time', 'attendance_events', ['office_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index(op.f('ix_attendance_events_organization_id'), 'attendance_events', ['organization_id'], unique=False)
    op.create_index('ix_attendance_events_qr_point_time', 'attendance_events', ['qr_point_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('ix_attendance_events_status_time', 'attendance_events', ['verification_status', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('uq_attendance_events_client_event', 'attendance_events', ['employee_id', 'client_event_id'], unique=True, postgresql_where=sa.text('client_event_id IS NOT NULL'))
    op.create_index('uq_attendance_events_nonce', 'attendance_events', ['employee_id', 'qr_nonce_hash', 'event_type'], unique=True, postgresql_where=sa.text("qr_nonce_hash IS NOT NULL AND verification_status = 'ACCEPTED'"))
    op.create_table('attendance_sessions',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('office_id', sa.UUID(), nullable=False),
    sa.Column('entry_event_id', sa.UUID(), nullable=False),
    sa.Column('exit_event_id', sa.UUID(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('calculated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status <> 'CLOSED' OR (ended_at IS NOT NULL AND exit_event_id IS NOT NULL)", name=op.f('ck_attendance_sessions_closed_has_exit')),
    sa.CheckConstraint("status IN ('OPEN', 'CLOSED', 'CORRECTED', 'INVALID')", name=op.f('ck_attendance_sessions_status')),
    sa.CheckConstraint('duration_seconds IS NULL OR duration_seconds >= 0', name=op.f('ck_attendance_sessions_duration_non_negative')),
    sa.CheckConstraint('ended_at IS NULL OR ended_at >= started_at', name=op.f('ck_attendance_sessions_end_after_start')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_attendance_sessions_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['entry_event_id'], ['attendance_events.id'], name=op.f('fk_attendance_sessions_entry_event_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['exit_event_id'], ['attendance_events.id'], name=op.f('fk_attendance_sessions_exit_event_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_attendance_sessions_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_attendance_sessions_organization_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attendance_sessions'))
    )
    op.create_index('ix_attendance_sessions_employee_time', 'attendance_sessions', ['employee_id', sa.literal_column('started_at DESC')], unique=False)
    op.create_index('ix_attendance_sessions_office_time', 'attendance_sessions', ['office_id', sa.literal_column('started_at DESC')], unique=False)
    op.create_index(op.f('ix_attendance_sessions_organization_id'), 'attendance_sessions', ['organization_id'], unique=False)
    op.create_index('uq_attendance_sessions_one_open', 'attendance_sessions', ['employee_id'], unique=True, postgresql_where=sa.text("status = 'OPEN'"))
    op.create_table('attendance_correction_requests',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('attendance_session_id', sa.UUID(), nullable=True),
    sa.Column('requested_entry_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requested_exit_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reviewed_by_user_id', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_comment', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('DRAFT', 'SUBMITTED', 'IN_REVIEW', 'APPROVED', 'REJECTED', 'CANCELLED')", name=op.f('ck_attendance_correction_requests_status')),
    sa.CheckConstraint('requested_entry_at IS NOT NULL OR requested_exit_at IS NOT NULL', name=op.f('ck_attendance_correction_requests_something_requested')),
    sa.CheckConstraint('requested_exit_at IS NULL OR requested_entry_at IS NULL OR requested_exit_at >= requested_entry_at', name=op.f('ck_attendance_correction_requests_exit_after_entry')),
    sa.ForeignKeyConstraint(['attendance_session_id'], ['attendance_sessions.id'], name=op.f('fk_attendance_correction_requests_attendance_session_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_attendance_correction_requests_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_attendance_correction_requests_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reviewed_by_user_id'], ['users.id'], name=op.f('fk_attendance_correction_requests_reviewed_by_user_id'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attendance_correction_requests'))
    )
    op.create_index(op.f('ix_attendance_correction_requests_employee_id'), 'attendance_correction_requests', ['employee_id'], unique=False)
    op.create_index(op.f('ix_attendance_correction_requests_organization_id'), 'attendance_correction_requests', ['organization_id'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # таблицы удаляются в обратном порядке зависимостей;
    # индексы и ограничения уходят вместе со своими таблицами
    op.drop_table('attendance_correction_requests')
    op.drop_table('attendance_sessions')
    op.drop_table('attendance_events')
    op.drop_table('qr_display_sessions')
    op.drop_table('employee_questions')
    op.drop_table('employee_assignments')
    op.drop_table('employee_absences')
    op.drop_table('absence_documents')
    op.drop_table('absence_actions')
    op.drop_table('user_role_scopes')
    op.drop_table('schedule_breaks')
    op.drop_table('office_qr_points')
    op.drop_table('office_networks')
    op.drop_table('knowledge_articles')
    op.drop_table('files')
    op.drop_table('employee_schedule_assignments')
    op.drop_table('employee_office_access')
    op.drop_table('departments')
    op.drop_table('calendar_exceptions')
    op.drop_table('audit_logs')
    op.drop_table('absence_requests')
    op.drop_table('users')
    op.drop_table('telegram_accounts')
    op.drop_table('schedule_days')
    op.drop_table('role_permissions')
    op.drop_table('offices')
    op.drop_table('notifications')
    op.drop_table('leave_balances')
    op.drop_table('employee_devices')
    op.drop_table('work_schedules')
    op.drop_table('roles')
    op.drop_table('regions')
    op.drop_table('positions')
    op.drop_table('organization_settings')
    op.drop_table('employees')
    op.drop_table('absence_types')
    op.drop_table('permissions')
    op.drop_table('organizations')
