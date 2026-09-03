"""Вопросы сотрудников и ответы на них.

Отличается от `unanswered_questions` AI-модуля: там кластеры формулировок
для пополнения базы знаний, здесь — конкретное обращение конкретного человека.
"""

from __future__ import annotations

from django.db import models

from humotech.core.constraints import raw_check
from humotech.core.enums import QUESTION_STATUSES, choices, status_check
from humotech.core.models import (
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class EmployeeQuestion(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="questions",
    )
    question_text = models.TextField()
    normalized_topic = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=30, choices=choices(QUESTION_STATUSES))
    ai_answer_text = models.TextField(null=True, blank=True)
    # Оценку считает приложение по результатам поиска; у модели уверенность
    # не спрашиваем принципиально.
    ai_confidence = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )
    answer_source = models.ForeignKey(
        "knowledge.KnowledgeSource",
        on_delete=models.PROTECT,
        db_column="answer_source_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    hr_answer_text = models.TextField(null=True, blank=True)
    assigned_to_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="assigned_to_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "employee_questions"
        verbose_name = "вопрос сотрудника"
        verbose_name_plural = "вопросы сотрудников"
        constraints = [
            status_check("status", QUESTION_STATUSES, "ck_employee_questions_status"),
            raw_check(
                "ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1)",
                "ck_employee_questions_confidence_range",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_employee_questions_organization_id"
            ),
            models.Index(
                fields=["employee"], name="ix_employee_questions_employee_id"
            ),
            models.Index(
                fields=["organization", "status"],
                name="ix_employee_questions_org_status",
            ),
        ]

    def __str__(self) -> str:
        return self.question_text[:60]
