"""Работа ассистента: неотвеченные вопросы, журнал обращений, оценки ответов."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    ANSWER_FEEDBACK_RATINGS,
    LLM_QUERY_STATUSES,
    UNANSWERED_QUESTION_STATUSES,
    choices,
    status_check,
)
from humotech.core.functions import TransactionNow
from humotech.core.models import (
    CreatedAtModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class UnansweredQuestion(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Кластер одинаковых по смыслу вопросов, на которые ответа не нашлось.

    Кластер, а не отдельное обращение: HR важно, что вопрос задали сорок раз,
    а не сорок одинаковых строк. Ключ кластера — нормализованный хеш вопроса
    вместе с языком и областью.
    """

    normalized_hash = models.CharField(max_length=64)
    question_text = models.TextField()
    language = models.CharField(max_length=10)
    occurrences_count = models.IntegerField(db_default=1)
    first_asked_at = models.DateTimeField(db_default=TransactionNow())
    last_asked_at = models.DateTimeField(db_default=TransactionNow())
    best_retrieval_score = models.DecimalField(
        max_digits=6, decimal_places=5, null=True, blank=True
    )
    status = models.CharField(
        max_length=20, choices=choices(UNANSWERED_QUESTION_STATUSES)
    )
    region = models.ForeignKey(
        "regions.Region",
        on_delete=models.PROTECT,
        db_column="region_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    # Последний спросивший — для связи, а не для учёта: хранить всех
    # спросивших значило бы держать персональные данные без нужды.
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    assigned_to_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="assigned_to_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    resolved_faq = models.ForeignKey(
        "knowledge.FaqEntry",
        on_delete=models.SET_NULL,
        db_column="resolved_faq_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    resolution_note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "unanswered_questions"
        verbose_name = "неотвеченный вопрос"
        verbose_name_plural = "неотвеченные вопросы"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "normalized_hash", "language", "office",
                        "region"],
                name="uq_unanswered_questions_cluster",
            ),
            status_check(
                "status", UNANSWERED_QUESTION_STATUSES,
                "ck_unanswered_questions_status",
            ),
            raw_check(
                "occurrences_count > 0", "ck_unanswered_questions_occurrences_positive"
            ),
            raw_check(
                "best_retrieval_score IS NULL "
                "OR (best_retrieval_score >= 0 AND best_retrieval_score <= 1)",
                "ck_unanswered_questions_score_range",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_unanswered_questions_organization_id"
            ),
            models.Index(
                fields=["organization", "status"], name="ix_unanswered_questions_status"
            ),
            # Самые частые — сверху: именно с них HR начинает пополнять базу.
            models.Index(
                fields=["organization", "-occurrences_count"],
                name="ix_unanswered_questions_top",
            ),
        ]

    def __str__(self) -> str:
        return self.question_text[:60]


class LlmQueryLog(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Журнал обращений к ассистенту.

    Текст вопроса и ответа обезличивается по расписанию: `anonymized_at`
    отмечает, что персональная часть уже удалена. Частичный индекс по
    необезличенным строкам держит в себе только то, что ещё предстоит обработать.
    """

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    region = models.ForeignKey(
        "regions.Region",
        on_delete=models.PROTECT,
        db_column="region_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    question_text = models.TextField(null=True, blank=True)
    answer_text = models.TextField(null=True, blank=True)
    language = models.CharField(max_length=10)
    status = models.CharField(max_length=20, choices=choices(LLM_QUERY_STATUSES))
    model = models.CharField(max_length=100, null=True, blank=True)
    prompt_version = models.CharField(max_length=20, null=True, blank=True)
    retrieval_score = models.DecimalField(
        max_digits=6, decimal_places=5, null=True, blank=True
    )
    retrieved_source_ids = models.JSONField(null=True, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    cache_hit = models.BooleanField(db_default=False)
    fallback_used = models.BooleanField(db_default=False)
    error_code = models.CharField(max_length=50, null=True, blank=True)
    anonymized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "llm_query_logs"
        verbose_name = "обращение к ассистенту"
        verbose_name_plural = "обращения к ассистенту"
        constraints = [
            status_check("status", LLM_QUERY_STATUSES, "ck_llm_query_logs_status"),
            raw_check(
                "input_tokens IS NULL OR input_tokens >= 0",
                "ck_llm_query_logs_input_tokens_valid",
            ),
            raw_check(
                "output_tokens IS NULL OR output_tokens >= 0",
                "ck_llm_query_logs_output_tokens_valid",
            ),
            raw_check(
                "latency_ms IS NULL OR latency_ms >= 0",
                "ck_llm_query_logs_latency_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_llm_query_logs_organization_id"
            ),
            models.Index(
                fields=["organization", "-created_at"], name="ix_llm_query_logs_org_time"
            ),
            models.Index(
                fields=["organization", "status"], name="ix_llm_query_logs_status"
            ),
            models.Index(
                fields=["created_at"],
                condition=Q(anonymized_at__isnull=True),
                name="ix_llm_query_logs_retention",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.status} {self.created_at:%Y-%m-%d %H:%M}"


class AnswerFeedback(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Оценка ответа сотрудником. Один сотрудник — одна оценка на обращение."""

    query_log = models.ForeignKey(
        LlmQueryLog,
        on_delete=models.PROTECT,
        db_column="query_log_id",
        db_index=False,
        related_name="feedback",
    )
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    rating = models.CharField(max_length=20, choices=choices(ANSWER_FEEDBACK_RATINGS))
    comment = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "answer_feedback"
        verbose_name = "оценка ответа"
        verbose_name_plural = "оценки ответов"
        constraints = [
            models.UniqueConstraint(
                fields=["query_log", "employee"], name="uq_answer_feedback_once"
            ),
            status_check(
                "rating", ANSWER_FEEDBACK_RATINGS, "ck_answer_feedback_rating"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_answer_feedback_organization_id"
            ),
            models.Index(fields=["query_log"], name="ix_answer_feedback_query_log_id"),
        ]

    def __str__(self) -> str:
        return self.rating
