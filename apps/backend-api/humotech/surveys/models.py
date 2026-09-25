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
    SURVEY_TEMPLATE_STATUSES,
    SURVEY_TRIGGER_KINDS,
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
    #: Черновик правят свободно; опубликованный — только новой
    #: редакцией. По архивному новых рассылок не создать, но старые
    #: живут: их ответы никуда не делись.
    status = models.CharField(
        max_length=20,
        choices=choices(SURVEY_TEMPLATE_STATUSES),
        db_default="DRAFT",
    )
    #: Номер редакции. Рассылка запоминает его у себя: по какому
    #: набору вопросов спрашивали, должно быть видно и через год.
    version = models.IntegerField(db_default=1)
    published_at = models.DateTimeField(null=True, blank=True)
    #: Прежняя редакция, из которой этот шаблон вырос. По цепочке
    #: видно, как вопросы менялись.
    previous_version = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        db_column="previous_version_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
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
        constraints = [
            status_check(
                "status", SURVEY_TEMPLATE_STATUSES, "ck_survey_templates_status"
            ),
            raw_check("version > 0", "ck_survey_templates_version_positive"),
            raw_check(
                "status <> 'PUBLISHED' OR published_at IS NOT NULL",
                "ck_survey_templates_published_has_time",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_survey_templates_organization_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} (ред. {self.version})"


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
    #: Редакция шаблона на момент отправки. Вопросы потом могут
    #: измениться новой редакцией — в отчёте по этой рассылке должно
    #: остаться то, что спрашивали на самом деле.
    template_version = models.IntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=choices(SURVEY_CAMPAIGN_STATUSES)
    )
    #: Когда напомнить тем, кто не закончил, и когда закрыть приём.
    #: Пусто — напоминания нет и срок не ограничен.
    remind_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)
    #: Повод, по которому автоматизация завела эту рассылку: правило и
    #: день события. Ключ нужен, чтобы «один опрос на одно событие»
    #: держалось базой, а не памятью воркера: два запуска в один день,
    #: перезапуск очереди и второй воркер — три разные истории с одним
    #: исходом, и разбирать их по заголовку значит однажды не разобрать.
    #: У рассылки, заведённой руками, пусто.
    trigger_key = models.CharField(max_length=255, null=True, blank=True)
    #: Автоматизация, которая эту рассылку породила. Пусто — её
    #: создал человек руками.
    automation = models.ForeignKey(
        "SurveyAutomation",
        on_delete=models.SET_NULL,
        db_column="automation_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="campaigns",
    )
    audience_kind = models.CharField(
        max_length=20, choices=choices(SURVEY_AUDIENCE_KINDS)
    )
    #: Идентификаторы сотрудников, отделов или офисов — по виду круга.
    audience_ids = models.JSONField(null=True, blank=True)
    #: Кого спросят — списком людей на момент создания рассылки.
    #:
    #: Запланированная рассылка уходит позже, и за это время состав
    #: отдела меняется. Круг, который кадровик видел на проверке, — это
    #: обещание; пересчитать его в день отправки значило бы спросить не
    #: тех, кого он утвердил. Пусто — у повторяющихся и у заведённых
    #: правилом: им круг нужен на каждый день свой.
    audience_snapshot = models.JSONField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    #: Повтор раз в N месяцев. NULL — разовая рассылка.
    repeat_months = models.IntegerField(null=True, blank=True)
    #: Когда рассылать в следующий раз. У разовой пусто после отправки.
    next_send_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    #: Анонимный опрос: кадровик видит только сводку — ни ответов по
    #: людям, ни имён в выгрузке. Ставится при создании и больше не
    #: меняется: снять анонимность после ответов значило бы раскрыть их.
    is_anonymous = models.BooleanField(db_default=False)
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
            # Один повод — одна рассылка. Условие на NOT NULL
            # обязательно: рассылок, заведённых руками, без ключа
            # сколько угодно, и они не должны мешать друг другу.
            models.UniqueConstraint(
                fields=["organization", "automation", "trigger_key"],
                condition=Q(trigger_key__isnull=False),
                name="uq_survey_campaigns_occasion",
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
    #: Почему пропустили. Человеческими словами: «нет привязки
    #: Telegram», «уже не работает». Без причины строка «Пропущен»
    #: оставляет кадровика гадать.
    skip_reason = models.CharField(max_length=255, null=True, blank=True)

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


class SurveyAutomation(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Правило: по такому событию отправить такой опрос.

    Третья сущность рядом с шаблоном и рассылкой, а не их разновидность.
    Шаблон — что спрашивают, рассылка — кого спросили в конкретный день,
    автоматизация — почему спросят завтра. Смешать её с рассылкой значит
    получить рассылку без получателей: их ещё нет и не будет до события.

    Круг людей выбирается В МОМЕНТ СОБЫТИЯ, а не при создании правила:
    к концу стажировки отдел у человека бывает уже другой.
    """

    title = models.CharField(max_length=255)
    template = models.ForeignKey(
        SurveyTemplate,
        on_delete=models.PROTECT,
        db_column="template_id",
        db_index=False,
        related_name="automations",
    )
    trigger_kind = models.CharField(
        max_length=20, choices=choices(SURVEY_TRIGGER_KINDS)
    )
    #: Сдвиг от события в днях: 1 — на следующий день, 0 — в день
    #: события, 30 — через месяц. У расписания смысла не имеет.
    offset_days = models.IntegerField(db_default=0)
    #: Час и минута отправки в поясе организации.
    send_hour = models.IntegerField(db_default=10)
    send_minute = models.IntegerField(db_default=0)
    #: Для «регулярно»: раз в столько месяцев.
    repeat_months = models.IntegerField(null=True, blank=True)
    #: Кого сузить: офисы, отделы, должности. Пусто — всех, кто подошёл
    #: под событие.
    scope = models.JSONField(null=True, blank=True)
    is_active = models.BooleanField(db_default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
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
        db_table = "survey_automations"
        verbose_name = "автоматизация опроса"
        verbose_name_plural = "автоматизации опросов"
        constraints = [
            status_check(
                "trigger_kind", SURVEY_TRIGGER_KINDS,
                "ck_survey_automations_trigger_kind",
            ),
            raw_check(
                "send_hour BETWEEN 0 AND 23",
                "ck_survey_automations_send_hour_range",
            ),
            raw_check(
                "send_minute BETWEEN 0 AND 59",
                "ck_survey_automations_send_minute_range",
            ),
            # Сдвиг назад от события бессмысленен: спрашивать об итогах
            # стажировки за три дня до её конца нечего.
            raw_check(
                "offset_days >= 0", "ck_survey_automations_offset_non_negative"
            ),
            raw_check(
                "repeat_months IS NULL OR repeat_months > 0",
                "ck_survey_automations_repeat_positive",
            ),
            # У расписания период обязателен, у события — не нужен.
            raw_check(
                "(trigger_kind = 'SCHEDULE' AND repeat_months IS NOT NULL) "
                "OR (trigger_kind <> 'SCHEDULE' AND repeat_months IS NULL)",
                "ck_survey_automations_repeat_matches_kind",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_survey_automations_organization_id"
            ),
            models.Index(
                fields=["template"], name="ix_survey_automations_template_id"
            ),
            models.Index(
                fields=["is_active"],
                name="ix_survey_automations_active",
                condition=Q(is_active=True),
            ),
        ]

    def __str__(self) -> str:
        return self.title


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
