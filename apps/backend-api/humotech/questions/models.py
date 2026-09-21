"""Вопросы сотрудников и ответы на них.

Отличается от `unanswered_questions` AI-модуля: там кластеры формулировок
для пополнения базы знаний, здесь — конкретное обращение конкретного человека.

Обращение — это переписка, а не пара «вопрос — ответ». Сотрудник
уточняет, кадровик просит документ, человек присылает ещё сообщение.
Поэтому реплики лежат отдельной таблицей `employee_question_messages`,
а у самого обращения — только его состояние: кто ведёт, до какого срока
ответить, прочитано ли последнее сообщение.
"""

from __future__ import annotations

from django.db import models

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    QUESTION_CATEGORIES,
    QUESTION_DRAFT_STATUSES,
    QUESTION_EVENTS,
    QUESTION_MESSAGE_KINDS,
    QUESTION_MESSAGE_SOURCES,
    QUESTION_PRIORITIES,
    QUESTION_STATUSES,
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
    #: Номер обращения внутри организации. По нему ищут и на него ссылаются
    #: в разговоре: «обращение 214», а не идентификатор из 36 символов.
    number = models.IntegerField()
    #: Первое сообщение сотрудника. Сама переписка — в сообщениях.
    question_text = models.TextField()
    #: Тема, коротко. Ставит кадровик или берётся из начала вопроса.
    normalized_topic = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=30, choices=choices(QUESTION_STATUSES))
    priority = models.CharField(
        max_length=20, choices=choices(QUESTION_PRIORITIES), db_default="NORMAL"
    )
    category = models.CharField(
        max_length=30, choices=choices(QUESTION_CATEGORIES), db_default="OTHER"
    )
    #: Канал, из которого пришло обращение. Пока он один.
    channel = models.CharField(max_length=20, db_default="TELEGRAM")
    assigned_to_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="assigned_to_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    #: Ответить до. NULL — сейчас ответа никто не ждёт: кадровик уже
    #: ответил, ждёт сотрудника или вопрос закрыт.
    due_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(db_default=TransactionNow())
    #: Последнее сообщение сотрудника ещё не открывали в CRM.
    unread = models.BooleanField(db_default=False)
    #: Последнее слово за сотрудником: ответа на него ещё не было.
    awaiting_reply = models.BooleanField(db_default=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="closed_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    close_reason = models.TextField(null=True, blank=True)

    # --- черновик ассистента -------------------------------------------------
    # Черновик — подсказка кадровику и ничего больше: сотруднику он
    # не уходит никогда, отправляет только человек.
    ai_answer_text = models.TextField(null=True, blank=True)
    # Оценку считает приложение по результатам поиска; у модели уверенность
    # не спрашиваем принципиально.
    ai_confidence = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )
    ai_status = models.CharField(
        max_length=20, choices=choices(QUESTION_DRAFT_STATUSES),
        null=True, blank=True,
    )
    #: Все документы, на которых основан черновик, по порядку значимости.
    #: При конфликте — все спорящие.
    ai_source_ids = models.JSONField(null=True, blank=True)
    ai_generated_at = models.DateTimeField(null=True, blank=True)
    answer_source = models.ForeignKey(
        "knowledge.KnowledgeSource",
        on_delete=models.PROTECT,
        db_column="answer_source_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "employee_questions"
        verbose_name = "вопрос сотрудника"
        verbose_name_plural = "вопросы сотрудников"
        constraints = [
            status_check("status", QUESTION_STATUSES, "ck_employee_questions_status"),
            status_check(
                "priority", QUESTION_PRIORITIES, "ck_employee_questions_priority"
            ),
            status_check(
                "category", QUESTION_CATEGORIES, "ck_employee_questions_category"
            ),
            status_check(
                "ai_status", QUESTION_DRAFT_STATUSES,
                "ck_employee_questions_ai_status", nullable=True,
            ),
            raw_check(
                "ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1)",
                "ck_employee_questions_confidence_range",
            ),
            # Закрытое обращение знает, когда его закрыли. Без даты
            # «закрыто» нельзя ни проверить, ни посчитать.
            raw_check(
                "status <> 'CLOSED' OR closed_at IS NOT NULL",
                "ck_employee_questions_closed_has_date",
            ),
            models.UniqueConstraint(
                fields=["organization", "number"],
                name="uq_employee_questions_number",
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
            models.Index(
                fields=["organization", "last_message_at"],
                name="ix_employee_questions_org_last",
            ),
        ]

    def __str__(self) -> str:
        return self.question_text[:60]


class QuestionMessage(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Одна строка ленты: сообщение сотрудника, ответ HR или событие.

    Строки не меняются и не удаляются: лента — это то, что было. Исправить
    отправленное в Telegram сообщение нельзя, и делать вид, что можно,
    в CRM тоже нельзя.
    """

    question = models.ForeignKey(
        EmployeeQuestion,
        on_delete=models.CASCADE,
        db_column="question_id",
        db_index=False,
        related_name="messages",
    )
    kind = models.CharField(max_length=20, choices=choices(QUESTION_MESSAGE_KINDS))
    source = models.CharField(
        max_length=20, choices=choices(QUESTION_MESSAGE_SOURCES)
    )
    body = models.TextField(null=True, blank=True)
    event = models.CharField(
        max_length=30, choices=choices(QUESTION_EVENTS), null=True, blank=True
    )
    #: Подробности события: из какого состояния в какое, кому передано,
    #: причина закрытия. Для сообщений — пусто.
    details = models.JSONField(null=True, blank=True)
    author_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="author_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    author_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="author_employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    #: Строка очереди отправки ответа HR. Её состояние и есть доставка:
    #: «в очереди», «доставлено», «не доставлено».
    notification = models.ForeignKey(
        "notifications.Notification",
        on_delete=models.SET_NULL,
        db_column="notification_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    #: Ключ повтора от клиента: повторное нажатие «Отправить» не даёт
    #: второго сообщения человеку.
    client_request_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table = "employee_question_messages"
        verbose_name = "сообщение обращения"
        verbose_name_plural = "сообщения обращений"
        constraints = [
            status_check(
                "kind", QUESTION_MESSAGE_KINDS, "ck_question_messages_kind"
            ),
            status_check(
                "source", QUESTION_MESSAGE_SOURCES, "ck_question_messages_source"
            ),
            status_check(
                "event", QUESTION_EVENTS, "ck_question_messages_event",
                nullable=True,
            ),
            raw_check(
                "kind = 'SYSTEM' OR (body IS NOT NULL AND event IS NULL)",
                "ck_question_messages_body_or_event",
            ),
            models.UniqueConstraint(
                fields=["question", "client_request_id"],
                condition=models.Q(client_request_id__isnull=False),
                name="uq_question_messages_client_request",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"],
                name="ix_question_messages_organization_id",
            ),
            models.Index(
                fields=["question", "created_at"],
                name="ix_question_messages_question",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.event or ''}".strip()
