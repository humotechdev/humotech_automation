"""Таблицы, которые ведёт сам ассистент: неотвеченное, журнал и оценки.

`unanswered_questions` — не то же самое, что `employee_questions`.
    employee_questions   — тред одного сотрудника с ответом HR;
    unanswered_questions — ДЕДУПЛИЦИРОВАННЫЙ кластер вопросов, на которые
                           не нашлось подтверждённого ответа, с счётчиком
                           повторов. Материал, из которого HR делает FAQ.
Разная гранулярность и разный жизненный цикл, поэтому таблицы обе.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    Base,
    CreatedAtMixin,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import (
    ANSWER_FEEDBACK_RATINGS,
    LLM_QUERY_STATUSES,
    UNANSWERED_QUESTION_STATUSES,
    in_check,
)

if TYPE_CHECKING:
    pass


class UnansweredQuestion(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, Base
):
    """Кластер одинаковых по смыслу вопросов, оставшихся без ответа.

    Повторный такой же вопрос не создаёт новую строку, а увеличивает
    `occurrences_count` — иначе HR утонул бы в сотнях одинаковых обращений
    и не увидел бы, какой пробел в базе знаний действительно массовый.
    """

    __tablename__ = "unanswered_questions"

    # NULL допустим: вопрос мог прийти до привязки сотрудника
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="RESTRICT"), nullable=True
    )
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id", ondelete="RESTRICT"), nullable=True
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False)

    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256 нормализованного текста — ключ склейки повторов
    normalized_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurrences_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # лучший балл, которого удалось достичь поиском: показывает HR,
    # был ли вопрос «почти покрыт» или база знаний молчит совсем
    best_retrieval_score: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5), nullable=True
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_faq_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("faq_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        in_check("status", UNANSWERED_QUESTION_STATUSES, "status"),
        CheckConstraint("occurrences_count > 0", name="occurrences_positive"),
        CheckConstraint(
            "best_retrieval_score IS NULL OR "
            "(best_retrieval_score >= 0 AND best_retrieval_score <= 1)",
            name="score_range",
        ),
        # ключ дедупликации: один и тот же вопрос в одном языке и одной области
        UniqueConstraint(
            "organization_id", "normalized_hash", "language", "office_id", "region_id",
            name="uq_unanswered_questions_cluster",
        ),
        Index("ix_unanswered_questions_status", "organization_id", "status"),
        Index(
            "ix_unanswered_questions_top",
            "organization_id",
            text("occurrences_count DESC"),
        ),
    )


class LlmQueryLog(UUIDPrimaryKeyMixin, OrganizationScopedMixin, CreatedAtMixin, Base):
    """Технический журнал обращений. Неизменяемый: только INSERT.

    В журнал НЕ попадают: API-ключи, заголовки Authorization, содержимое
    системного промпта, персональные данные сверх самого вопроса.
    Текст вопроса проходит редактирование секретов перед записью.
    """

    __tablename__ = "llm_query_logs"

    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="RESTRICT"), nullable=True
    )
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id", ondelete="RESTRICT"), nullable=True
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False)

    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # имя модели пишем фактом: по журналу должно быть видно,
    # какая именно модель отвечала в тот день
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_score: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5), nullable=True
    )
    retrieved_source_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    cache_hit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    fallback_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # отметка о выполненной анонимизации по AI_LOG_RETENTION_DAYS
    anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        in_check("status", LLM_QUERY_STATUSES, "status"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="input_tokens_valid"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="output_tokens_valid"
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name="latency_valid"
        ),
        Index("ix_llm_query_logs_org_time", "organization_id", text("created_at DESC")),
        Index("ix_llm_query_logs_status", "organization_id", "status"),
        # для задачи анонимизации: что ещё не обезличено
        Index(
            "ix_llm_query_logs_retention",
            "created_at",
            postgresql_where=text("anonymized_at IS NULL"),
        ),
    )


class AnswerFeedback(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, CreatedAtMixin, Base
):
    """Оценка ответа сотрудником. Главный сигнал качества базы знаний."""

    __tablename__ = "answer_feedback"

    query_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_query_logs.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    query_log: Mapped["LlmQueryLog"] = relationship()

    __table_args__ = (
        in_check("rating", ANSWER_FEEDBACK_RATINGS, "rating"),
        # один сотрудник — одна оценка на ответ
        UniqueConstraint(
            "query_log_id", "employee_id", name="uq_answer_feedback_once"
        ),
    )
