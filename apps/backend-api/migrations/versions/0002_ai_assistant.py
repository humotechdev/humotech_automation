"""ai assistant: pgvector, knowledge_sources и таблицы ассистента

Что делает миграция:
  1. ставит расширение vector (эмбеддинги базы знаний);
  2. добавляет organizations.knowledge_revision — счётчик, по которому после
     публикации автоматически протухает весь кэш ответов;
  3. превращает knowledge_articles в knowledge_sources: таблица из 0001 была
     тем же самым документом, только без области действия, хешей и сроков.
     Дублирующую таблицу не заводим, FK из employee_questions перецеливаем;
  4. создаёт шесть новых таблиц ассистента.

Индексы под векторный поиск — HNSW, а не ivfflat: ivfflat строит списки
по уже существующим строкам и на пустой таблице бесполезен, а стартуем мы
именно с пустой базы знаний.

Revision ID: 0002_ai_assistant
Revises: 0001_initial_schema
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_ai_assistant"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMED_CONSTRAINTS = (
    ("pk_knowledge_articles", "pk_knowledge_sources"),
    ("fk_knowledge_articles_organization_id",
     "fk_knowledge_sources_organization_id"),
    ("fk_knowledge_articles_created_by_user_id",
     "fk_knowledge_sources_created_by_user_id"),
    ("fk_knowledge_articles_approved_by_user_id",
     "fk_knowledge_sources_approved_by_user_id"),
    ("ck_knowledge_articles_version_positive",
     "ck_knowledge_sources_version_positive"),
)


def upgrade() -> None:
    # --- 1. расширение для эмбеддингов ---
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- 2. счётчик ревизии базы знаний ---
    op.add_column(
        "organizations",
        sa.Column(
            "knowledge_revision", sa.Integer(), server_default="1", nullable=False
        ),
    )

    # --- 3. employee_questions: FK снимается до переименования таблицы ---
    op.drop_constraint(
        "fk_employee_questions_answer_source_article_id",
        "employee_questions",
        type_="foreignkey",
    )
    op.alter_column(
        "employee_questions",
        "answer_source_article_id",
        new_column_name="answer_source_id",
    )

    # --- 4. knowledge_articles -> knowledge_sources ---
    op.rename_table("knowledge_articles", "knowledge_sources")
    for old, new in RENAMED_CONSTRAINTS:
        op.execute(
            "ALTER TABLE knowledge_sources RENAME CONSTRAINT %s TO %s" % (old, new)
        )
    op.execute(
        "ALTER INDEX ix_knowledge_articles_organization_id "
        "RENAME TO ix_knowledge_sources_organization_id"
    )
    # старый составной индекс заменяется новым, с языком
    op.drop_index("ix_knowledge_articles_org_status", table_name="knowledge_sources")
    # Набор статусов изменился: DRAFT/IN_REVIEW/PUBLISHED/ARCHIVED
    # -> DRAFT/INDEXING/ACTIVE/ARCHIVED/ERROR.
    # Здесь сырой SQL, а не op.drop_constraint: Alembic прогнал бы имя через
    # конвенцию ck_%(table_name)s_%(constraint_name)s и получил бы
    # ck_knowledge_sources_ck_knowledge_articles_status — такого ограничения нет.
    op.execute(
        "ALTER TABLE knowledge_sources DROP CONSTRAINT ck_knowledge_articles_status"
    )
    # category переезжает в metadata, отдельная колонка не нужна
    op.drop_column("knowledge_sources", "category")

    # NOT NULL-колонки добавляются со значением по умолчанию, которое тут же
    # снимается: default нужен только на время ALTER, в схеме ему не место.
    op.add_column(
        "knowledge_sources",
        sa.Column(
            "source_type", sa.String(20), server_default="POLICY", nullable=False
        ),
    )
    op.alter_column("knowledge_sources", "source_type", server_default=None)

    op.add_column(
        "knowledge_sources",
        sa.Column("language", sa.String(10), server_default="ru", nullable=False),
    )
    op.alter_column("knowledge_sources", "language", server_default=None)

    op.add_column(
        "knowledge_sources",
        sa.Column(
            "content_hash",
            sa.String(64),
            server_default=sa.text("repeat('0', 64)"),
            nullable=False,
        ),
    )
    op.alter_column("knowledge_sources", "content_hash", server_default=None)

    op.add_column("knowledge_sources", sa.Column("office_id", sa.UUID(), nullable=True))
    op.add_column("knowledge_sources", sa.Column("region_id", sa.UUID(), nullable=True))
    op.add_column(
        "knowledge_sources", sa.Column("department_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "knowledge_sources", sa.Column("effective_from", sa.Date(), nullable=True)
    )
    op.add_column(
        "knowledge_sources", sa.Column("effective_to", sa.Date(), nullable=True)
    )
    op.add_column(
        "knowledge_sources",
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "knowledge_sources", sa.Column("parent_source_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "knowledge_sources",
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "knowledge_sources",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_knowledge_sources_office_id", "knowledge_sources", "offices",
        ["office_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_knowledge_sources_region_id", "knowledge_sources", "regions",
        ["region_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_knowledge_sources_department_id", "knowledge_sources", "departments",
        ["department_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_knowledge_sources_parent_source_id", "knowledge_sources",
        "knowledge_sources", ["parent_source_id"], ["id"], ondelete="RESTRICT",
    )

    # Передаётся ТОЛЬКО суффикс имени: конвенция проекта —
    # ck_%(table_name)s_%(constraint_name)s, полное имя дало бы двойной префикс
    # вида ck_knowledge_sources_ck_knowledge_sources_status.
    op.create_check_constraint(
        "status", "knowledge_sources", "status IN ('DRAFT', 'INDEXING', 'ACTIVE', 'ARCHIVED', 'ERROR')"
    )
    op.create_check_constraint(
        "source_type", "knowledge_sources", "source_type IN ('FAQ', 'POLICY', 'INSTRUCTION', 'DOCUMENT')"
    )
    op.create_check_constraint(
        "content_hash_length", "knowledge_sources", "char_length(content_hash) = 64"
    )
    op.create_check_constraint(
        "effective_period", "knowledge_sources",
        "effective_to IS NULL OR effective_from IS NULL "
        "OR effective_to >= effective_from",
    )
    op.create_check_constraint(
        "scope_not_both", "knowledge_sources",
        "office_id IS NULL OR region_id IS NULL",
    )
    op.create_check_constraint(
        "no_self_parent", "knowledge_sources",
        "parent_source_id IS NULL OR parent_source_id <> id",
    )

    op.create_index(
        "ix_knowledge_sources_fts", "knowledge_sources",
        [sa.literal_column("to_tsvector('simple', title || ' ' || content)")],
        unique=False, postgresql_using="gin",
    )
    op.create_index(
        "ix_knowledge_sources_lookup", "knowledge_sources",
        ["organization_id", "status", "language"], unique=False,
    )
    op.create_index(
        "ix_knowledge_sources_office_id", "knowledge_sources", ["office_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_sources_region_id", "knowledge_sources", ["region_id"],
        unique=False,
    )
    # активная версия документа в организации ровно одна
    op.create_index(
        "uq_knowledge_sources_active_lineage", "knowledge_sources",
        ["organization_id", "title", "language"], unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # --- 5. новые таблицы ассистента ---
    op.create_table('llm_query_logs',
    sa.Column('employee_id', sa.UUID(), nullable=True),
    sa.Column('office_id', sa.UUID(), nullable=True),
    sa.Column('region_id', sa.UUID(), nullable=True),
    sa.Column('language', sa.String(length=10), nullable=False),
    sa.Column('question_text', sa.Text(), nullable=True),
    sa.Column('answer_text', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('prompt_version', sa.String(length=20), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('retrieval_score', sa.Numeric(precision=6, scale=5), nullable=True),
    sa.Column('retrieved_source_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('cache_hit', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('fallback_used', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('error_code', sa.String(length=50), nullable=True),
    sa.Column('anonymized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('EXACT_FAQ', 'RAG_ANSWERED', 'ESCALATED', 'PERSONAL_DATA', 'ERROR')", name=op.f('ck_llm_query_logs_status')),
    sa.CheckConstraint('input_tokens IS NULL OR input_tokens >= 0', name=op.f('ck_llm_query_logs_input_tokens_valid')),
    sa.CheckConstraint('latency_ms IS NULL OR latency_ms >= 0', name=op.f('ck_llm_query_logs_latency_valid')),
    sa.CheckConstraint('output_tokens IS NULL OR output_tokens >= 0', name=op.f('ck_llm_query_logs_output_tokens_valid')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_llm_query_logs_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_llm_query_logs_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_llm_query_logs_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_llm_query_logs_region_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_query_logs'))
    )
    op.create_index('ix_llm_query_logs_org_time', 'llm_query_logs', ['organization_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index(op.f('ix_llm_query_logs_organization_id'), 'llm_query_logs', ['organization_id'], unique=False)
    op.create_index('ix_llm_query_logs_retention', 'llm_query_logs', ['created_at'], unique=False, postgresql_where=sa.text('anonymized_at IS NULL'))
    op.create_index('ix_llm_query_logs_status', 'llm_query_logs', ['organization_id', 'status'], unique=False)
    op.create_table('answer_feedback',
    sa.Column('query_log_id', sa.UUID(), nullable=False),
    sa.Column('employee_id', sa.UUID(), nullable=True),
    sa.Column('rating', sa.String(length=20), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("rating IN ('HELPFUL', 'NOT_HELPFUL')", name=op.f('ck_answer_feedback_rating')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_answer_feedback_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_answer_feedback_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['query_log_id'], ['llm_query_logs.id'], name=op.f('fk_answer_feedback_query_log_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_answer_feedback')),
    sa.UniqueConstraint('query_log_id', 'employee_id', name='uq_answer_feedback_once')
    )
    op.create_index(op.f('ix_answer_feedback_organization_id'), 'answer_feedback', ['organization_id'], unique=False)
    op.create_index(op.f('ix_answer_feedback_query_log_id'), 'answer_feedback', ['query_log_id'], unique=False)
    op.create_table('faq_entries',
    sa.Column('canonical_question', sa.Text(), nullable=False),
    sa.Column('approved_answer', sa.Text(), nullable=False),
    sa.Column('question_embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
    sa.Column('source_id', sa.UUID(), nullable=True),
    sa.Column('language', sa.String(length=10), nullable=False),
    sa.Column('office_id', sa.UUID(), nullable=True),
    sa.Column('region_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=False),
    sa.Column('approved_by_user_id', sa.UUID(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')", name=op.f('ck_faq_entries_status')),
    sa.CheckConstraint('char_length(content_hash) = 64', name=op.f('ck_faq_entries_content_hash_length')),
    sa.CheckConstraint('office_id IS NULL OR region_id IS NULL', name=op.f('ck_faq_entries_scope_not_both')),
    sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], name=op.f('fk_faq_entries_approved_by_user_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_faq_entries_created_by_user_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_faq_entries_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_faq_entries_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_faq_entries_region_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['knowledge_sources.id'], name=op.f('fk_faq_entries_source_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_faq_entries'))
    )
    op.create_index('ix_faq_entries_embedding_cosine', 'faq_entries', ['question_embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'question_embedding': 'vector_cosine_ops'})
    op.create_index('ix_faq_entries_lookup', 'faq_entries', ['organization_id', 'status', 'language'], unique=False)
    op.create_index(op.f('ix_faq_entries_organization_id'), 'faq_entries', ['organization_id'], unique=False)
    op.create_table('knowledge_chunks',
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('chunk_text', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
    sa.Column('token_count', sa.Integer(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('chunk_index >= 0', name=op.f('ck_knowledge_chunks_chunk_index_non_negative')),
    sa.CheckConstraint('token_count > 0', name=op.f('ck_knowledge_chunks_token_count_positive')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_knowledge_chunks_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['knowledge_sources.id'], name=op.f('fk_knowledge_chunks_source_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_chunks')),
    sa.UniqueConstraint('source_id', 'chunk_index', name='uq_knowledge_chunks_index')
    )
    op.create_index('ix_knowledge_chunks_embedding_cosine', 'knowledge_chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index('ix_knowledge_chunks_fts', 'knowledge_chunks', [sa.literal_column("to_tsvector('simple', chunk_text)")], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_knowledge_chunks_organization_id'), 'knowledge_chunks', ['organization_id'], unique=False)
    op.create_index(op.f('ix_knowledge_chunks_source_id'), 'knowledge_chunks', ['source_id'], unique=False)
    op.create_table('knowledge_index_jobs',
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error_summary', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')", name=op.f('ck_knowledge_index_jobs_status')),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_knowledge_index_jobs_attempts_non_negative')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_knowledge_index_jobs_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['knowledge_sources.id'], name=op.f('fk_knowledge_index_jobs_source_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_index_jobs'))
    )
    op.create_index(op.f('ix_knowledge_index_jobs_organization_id'), 'knowledge_index_jobs', ['organization_id'], unique=False)
    op.create_index('ix_knowledge_index_jobs_queue', 'knowledge_index_jobs', ['next_attempt_at'], unique=False, postgresql_where=sa.text("status = 'QUEUED'"))
    op.create_index(op.f('ix_knowledge_index_jobs_source_id'), 'knowledge_index_jobs', ['source_id'], unique=False)
    op.create_table('unanswered_questions',
    sa.Column('employee_id', sa.UUID(), nullable=True),
    sa.Column('office_id', sa.UUID(), nullable=True),
    sa.Column('region_id', sa.UUID(), nullable=True),
    sa.Column('language', sa.String(length=10), nullable=False),
    sa.Column('question_text', sa.Text(), nullable=False),
    sa.Column('normalized_hash', sa.String(length=64), nullable=False),
    sa.Column('occurrences_count', sa.Integer(), server_default='1', nullable=False),
    sa.Column('best_retrieval_score', sa.Numeric(precision=6, scale=5), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('assigned_to_user_id', sa.UUID(), nullable=True),
    sa.Column('resolved_faq_id', sa.UUID(), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('first_asked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_asked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('NEW', 'IN_REVIEW', 'ANSWERED', 'IGNORED')", name=op.f('ck_unanswered_questions_status')),
    sa.CheckConstraint('best_retrieval_score IS NULL OR (best_retrieval_score >= 0 AND best_retrieval_score <= 1)', name=op.f('ck_unanswered_questions_score_range')),
    sa.CheckConstraint('occurrences_count > 0', name=op.f('ck_unanswered_questions_occurrences_positive')),
    sa.ForeignKeyConstraint(['assigned_to_user_id'], ['users.id'], name=op.f('fk_unanswered_questions_assigned_to_user_id'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_unanswered_questions_employee_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], name=op.f('fk_unanswered_questions_office_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_unanswered_questions_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_unanswered_questions_region_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['resolved_faq_id'], ['faq_entries.id'], name=op.f('fk_unanswered_questions_resolved_faq_id'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_unanswered_questions')),
    sa.UniqueConstraint('organization_id', 'normalized_hash', 'language', 'office_id', 'region_id', name='uq_unanswered_questions_cluster')
    )
    op.create_index(op.f('ix_unanswered_questions_organization_id'), 'unanswered_questions', ['organization_id'], unique=False)
    op.create_index('ix_unanswered_questions_status', 'unanswered_questions', ['organization_id', 'status'], unique=False)
    op.create_index('ix_unanswered_questions_top', 'unanswered_questions', ['organization_id', sa.literal_column('occurrences_count DESC')], unique=False)
    # ### end Alembic commands ###

    # --- 6. FK employee_questions возвращается уже на knowledge_sources ---
    op.create_foreign_key(
        "fk_employee_questions_answer_source_id", "employee_questions",
        "knowledge_sources", ["answer_source_id"], ["id"], ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_employee_questions_answer_source_id", "employee_questions",
        type_="foreignkey",
    )

    for table in (
        "answer_feedback", "llm_query_logs", "unanswered_questions",
        "knowledge_index_jobs", "faq_entries", "knowledge_chunks",
    ):
        op.drop_table(table)

    for name in (
        "uq_knowledge_sources_active_lineage",
        "ix_knowledge_sources_region_id",
        "ix_knowledge_sources_office_id",
        "ix_knowledge_sources_lookup",
        "ix_knowledge_sources_fts",
    ):
        op.drop_index(name, table_name="knowledge_sources")

    # снова только суффиксы — по той же причине, что и при создании
    for name in (
        "no_self_parent", "scope_not_both", "effective_period",
        "content_hash_length", "source_type", "status",
    ):
        op.drop_constraint(name, "knowledge_sources", type_="check")

    for name in (
        "fk_knowledge_sources_parent_source_id",
        "fk_knowledge_sources_department_id",
        "fk_knowledge_sources_region_id",
        "fk_knowledge_sources_office_id",
    ):
        op.drop_constraint(name, "knowledge_sources", type_="foreignkey")

    for name in (
        "published_at", "metadata", "parent_source_id", "priority",
        "effective_to", "effective_from", "department_id", "region_id",
        "office_id", "content_hash", "language", "source_type",
    ):
        op.drop_column("knowledge_sources", name)

    op.add_column(
        "knowledge_sources", sa.Column("category", sa.String(100), nullable=True)
    )
    # сырой SQL: имя должно остаться ровно тем, что было в миграции 0001
    op.execute(
        "ALTER TABLE knowledge_sources ADD CONSTRAINT ck_knowledge_articles_status "
        "CHECK (status IN ('DRAFT', 'IN_REVIEW', 'PUBLISHED', 'ARCHIVED'))"
    )
    op.create_index(
        "ix_knowledge_articles_org_status", "knowledge_sources",
        ["organization_id", "status"], unique=False,
    )
    op.execute(
        "ALTER INDEX ix_knowledge_sources_organization_id "
        "RENAME TO ix_knowledge_articles_organization_id"
    )
    for old, new in RENAMED_CONSTRAINTS:
        op.execute(
            "ALTER TABLE knowledge_sources RENAME CONSTRAINT %s TO %s" % (new, old)
        )
    op.rename_table("knowledge_sources", "knowledge_articles")

    op.alter_column(
        "employee_questions", "answer_source_id",
        new_column_name="answer_source_article_id",
    )
    op.create_foreign_key(
        "fk_employee_questions_answer_source_article_id", "employee_questions",
        "knowledge_articles", ["answer_source_article_id"], ["id"],
        ondelete="RESTRICT",
    )

    op.drop_column("organizations", "knowledge_revision")
