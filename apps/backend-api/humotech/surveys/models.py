"""Опросы сотрудников: шаблон, рассылка, получатель, ответ.

Опрос здесь **именной**, и это решение, а не недоработка. HR видит, кто
и что ответил: по этим ответам он идёт разговаривать с человеком, а не
считает настроение в среднем по офису. Анонимный опрос — другой продукт
с другими гарантиями, и подмешивать его сюда нельзя: сотрудник должен
понимать, что подписывается своим именем.

Разделение на шаблон и рассылку тоже не формальность. Шаблон — текст
вопросов, он живёт долго и повторяется; рассылка — одно обращение к
названному кругу людей в назначенное время. Правка шаблона не меняет
того, что уже спросили: у рассылки свой снимок вопросов через ссылку на
шаблон, а вопросы шаблона не удаляются, пока на них есть ответы —
внешние ключи объявлены `ON DELETE RESTRICT`.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    SURVEY_AUDIENCE_KINDS,
    SURVEY_CAMPAIGN_STATUSES,
    SURVEY_QUESTION_KINDS,
    SURVEY_RECIPIENT_STATUSES,
    choices,
    status_check,
)
from humotech.core.models import (
    ArchivableModel,
    CreatedAtModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class SurveyTemplate(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    """Набор вопросов, который переиспользуют."""

    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="created_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "survey_templates"
        verbose_name = "шаблон опроса"
        verbose_name_plural = "шаблоны опросов"
        indexes = [
            models.Index(
                fields=["organization"], name="ix_survey_templates_organization_id"
            ),
        ]

    def __str__(self) -> str:
        return self.title


class SurveyQuestion(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Вопрос шаблона.

    Варианты ответа лежат списком в `options`: у вопроса их единицы, и
    отдельная таблица на две строки дала бы join там, где хватает
    колонки. Для шкалы и свободного текста вариантов нет вовсе —
    ограничение в базе это и проверяет.
    """

    template = models.ForeignKey(
        SurveyTemplate,
        on_delete=models.PROTECT,
        db_column="template_id",
        db_index=False,
        related_name="questions",
    )
    position = models.IntegerField()
    text = models.TextField()
    kind = models.CharField(max_length=20, choices=choices(SURVEY_QUESTION_KINDS))
    is_required = models.BooleanField(db_default=True)
    options = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "survey_questions"
        verbose_name = "вопрос опроса"
        verbose_name_plural = "вопросы опроса"
        constraints = [
            status_check("kind", SURVEY_QUESTION_KINDS, "ck_survey_questions_kind"),
            raw_check("position > 0", "ck_survey_questions_position_positive"),
            # У вопроса с вариантами они обязаны быть, у остальных —
            # обязаны отсутствовать: пустой список вариантов у шкалы
            # означал бы, что кто-то собирался их туда положить.
            raw_check(
                "(kind IN ('SINGLE', 'MULTI') AND options IS NOT NULL) "
                "OR (kind IN ('SCALE', 'TEXT') AND options IS NULL)",
                "ck_survey_questions_options_match_kind",
            ),
            models.UniqueConstraint(
                fields=["template", "position"],
                name="uq_survey_questions_template_position",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_survey_questions_organization_id"
            ),
            models.Index(fields=["template"], name="ix_survey_questions_template_id"),
        ]

    def __str__(self) -> str:
        return f"{self.position}. {self.text[:40]}"


class SurveyCampaign(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Одно обращение к названному кругу людей.

    Круг задаётся видом и списком идентификаторов: сотрудники, отдел,
    офис или все действующие. Раскрывается он в строки получателей в
    момент отправки, а не при создании: за неделю до запланированной
    даты состав отдела меняется, и спрашивать надо тех, кто работает
    сейчас.
    """

    template = models.ForeignKey(
        SurveyTemplate,
        on_delete=models.PROTECT,
        db_column="template_id",
        db_index=False,
        related_name="campaigns",
    )
    title = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20, choices=choices(SURVEY_CAMPAIGN_STATUSES)
    )
    audience_kind = models.CharField(
        max_length=20, choices=choices(SURVEY_AUDIENCE_KINDS)
    )
    #: Идентификаторы сотрудников, отделов или офисов — по виду круга.
    audience_ids = models.JSONField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    #: Повтор раз в N месяцев. NULL — разовая рассылка.
    repeat_months = models.IntegerField(null=True, blank=True)
    #: Когда рассылать в следующий раз. У разовой пусто после отправки.
    next_send_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="created_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "survey_campaigns"
        verbose_name = "рассылка опроса"
        verbose_name_plural = "рассылки опросов"
        constraints = [
            status_check(
                "status", SURVEY_CAMPAIGN_STATUSES, "ck_survey_campaigns_status"
            ),
            status_check(
                "audience_kind", SURVEY_AUDIENCE_KINDS,
                "ck_survey_campaigns_audience_kind",
            ),
            # Повтор — раз в два или три месяца: других периодов у HR нет,
            # а произвольное число месяцев превратило бы рассылку в
            # расписание, которого никто не читает.
            raw_check(
                "repeat_months IS NULL OR repeat_months IN (2, 3)",
                "ck_survey_campaigns_repeat_months",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_survey_campaigns_organization_id"
            ),
            models.Index(fields=["template"], name="ix_survey_campaigns_template_id"),
            models.Index(
                fields=["next_send_at"],
                name="ix_survey_campaigns_next_send_at",
                condition=Q(next_send_at__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        return self.title


class SurveyRecipient(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Один сотрудник в одной рассылке и его состояние.

    Состояние ставит сервер: отправлено — когда уведомление ушло в
    очередь, начал — когда открыл опрос, завершил — когда прислал
    ответы. Клиент таких параметров не присылает.
    """

    campaign = models.ForeignKey(
        SurveyCampaign,
        on_delete=models.PROTECT,
        db_column="campaign_id",
        db_index=False,
        related_name="recipients",
    )
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="survey_recipients",
    )
    status = models.CharField(
        max_length=20, choices=choices(SURVEY_RECIPIENT_STATUSES)
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "survey_recipients"
        verbose_name = "получатель опроса"
        verbose_name_plural = "получатели опроса"
        constraints = [
            status_check(
                "status", SURVEY_RECIPIENT_STATUSES, "ck_survey_recipients_status"
            ),
            raw_check(
                "status <> 'COMPLETED' OR completed_at IS NOT NULL",
                "ck_survey_recipients_completed_has_time",
            ),
            models.UniqueConstraint(
                fields=["campaign", "employee"],
                name="uq_survey_recipients_campaign_employee",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_survey_recipients_organization_id"
            ),
            models.Index(fields=["campaign"], name="ix_survey_recipients_campaign_id"),
            models.Index(fields=["employee"], name="ix_survey_recipients_employee_id"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} {self.status}"


class SurveyAnswer(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Ответ одного человека на один вопрос.

    Три колонки значения вместо одной строки на всё: по числу шкалы
    считают среднее, по выбранным вариантам — доли, а свободный текст
    читают. Сложенные в одну строку, они превратились бы в разбор текста
    при каждом подсчёте.
    """

    recipient = models.ForeignKey(
        SurveyRecipient,
        on_delete=models.PROTECT,
        db_column="recipient_id",
        db_index=False,
        related_name="answers",
    )
    question = models.ForeignKey(
        SurveyQuestion,
        on_delete=models.PROTECT,
        db_column="question_id",
        db_index=False,
        related_name="answers",
    )
    text = models.TextField(null=True, blank=True)
    number = models.IntegerField(null=True, blank=True)
    options = models.JSONField(null=True, blank=True)
    answered_at = models.DateTimeField()

    class Meta:
        db_table = "survey_answers"
        verbose_name = "ответ на вопрос"
        verbose_name_plural = "ответы на вопросы"
        constraints = [
            # Пустой ответ не хранится: у необязательного вопроса его
            # просто нет, и строка со всеми NULL означала бы «ответил
            # ничем».
            raw_check(
                "text IS NOT NULL OR number IS NOT NULL OR options IS NOT NULL",
                "ck_survey_answers_has_value",
            ),
            models.UniqueConstraint(
                fields=["recipient", "question"],
                name="uq_survey_answers_recipient_question",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_survey_answers_organization_id"
            ),
            models.Index(fields=["recipient"], name="ix_survey_answers_recipient_id"),
            models.Index(fields=["question"], name="ix_survey_answers_question_id"),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_id} → {self.question_id}"
