"""Уведомления сотрудникам: Telegram, почта, push, внутри интерфейса.

Таблица работает как transactional outbox. Строка создаётся в той же
транзакции, что и само изменение — заявка и уведомление о ней либо есть оба,
либо нет ни одного. Отправщик — отдельный процесс: он забирает строки
`SELECT ... FOR UPDATE SKIP LOCKED`, поэтому несколько отправщиков не берут
одну и ту же строку и не блокируют друг друга.

Очередь именно в PostgreSQL, без брокера: Celery в проекте нет, а гарантия
«не отправим то, чего не произошло» здесь важнее пропускной способности.
"""

from __future__ import annotations

from django.db import models

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    NOTIFICATION_ATTEMPT_OUTCOMES,
    NOTIFICATION_CHANNELS,
    NOTIFICATION_STATUSES,
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


class Notification(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="notifications",
    )
    channel = models.CharField(max_length=20, choices=choices(NOTIFICATION_CHANNELS))
    notification_type = models.CharField(max_length=100)
    title = models.CharField(max_length=255, null=True, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=choices(NOTIFICATION_STATUSES))
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    related_entity_type = models.CharField(max_length=100, null=True, blank=True)
    related_entity_id = models.UUIDField(null=True, blank=True)
    # --- очередь отправки --------------------------------------------------
    attempts = models.IntegerField(db_default=0)
    # Когда строку можно взять снова. NULL у PENDING означает «прямо сейчас».
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    # Момент захвата строки отправщиком. FOR UPDATE снимается при падении
    # процесса, а статус RUNNING — нет: без этой отметки такая строка
    # осталась бы в RUNNING навсегда, и её некому было бы переотправить.
    locked_at = models.DateTimeField(null=True, blank=True)
    # Ключ повтора: два одинаковых события дают одну строку, а не две
    # одинаковых записи в чате сотрудника.
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    # Полна ли история попыток в `notification_attempts`.
    #
    # Отдельный признак, а не вывод по данным. Вывести его нельзя ни из
    # чего: счётчик `attempts` обнуляется при ручном повторе, и строка
    # «попыток 0, записей 0» после повтора неотличима от только что
    # заведённой — хотя у одной история потеряна, а у другой её просто
    # ещё нет.
    #
    # Ставится один раз при заведении строки и больше не меняется НИКОГДА:
    # ни повтор, ни отмена, ни новая записанная попытка не делают
    # утраченное прошлое известным. False стоит у строк, заведённых до
    # появления таблицы попыток.
    attempt_history_complete = models.BooleanField(db_default=True)

    class Meta:
        db_table = "notifications"
        verbose_name = "уведомление"
        verbose_name_plural = "уведомления"
        constraints = [
            status_check("channel", NOTIFICATION_CHANNELS, "ck_notifications_channel"),
            status_check("status", NOTIFICATION_STATUSES, "ck_notifications_status"),
            raw_check("attempts >= 0", "ck_notifications_attempts_non_negative"),
            raw_check(
                "status <> 'RUNNING' OR locked_at IS NOT NULL",
                "ck_notifications_running_is_locked",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uq_notifications_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_notifications_organization_id"
            ),
            models.Index(
                fields=["employee", "created_at"],
                name="ix_notifications_employee_created",
            ),
            # Отправщик выбирает только то, что ещё не отправлено. Частичный
            # индекс держит в себе очередь, а не всю историю уведомлений.
            models.Index(
                fields=["scheduled_at"],
                condition=models.Q(status="PENDING"),
                name="ix_notifications_pending",
            ),
            # Отправщик упорядочивает очередь по next_attempt_at, а не по
            # scheduled_at: индекс выше такому запросу не помогает вовсе.
            models.Index(
                fields=["next_attempt_at"],
                condition=models.Q(status="PENDING"),
                name="ix_notifications_next_attempt",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.notification_type} -> {self.employee_id}"


class NotificationAttempt(UUIDPrimaryKeyModel, TimestampedModel):
    """Одна попытка отправки: когда, чем кончилась и почему.

    До этой таблицы у строки были только счётчик `attempts` и текст
    последней ошибки. По ним нельзя ответить на вопрос, который задают
    в разборе чаще всего: сообщение не уходит второй день или упало
    один раз ночью? Счётчик «3» одинаков в обоих случаях.

    Пишется РОВНО оттуда, где попытка заканчивается: `mark_sent`,
    `mark_failed` и ветка `claim`, снимающая строку без адресата. Ручной
    повтор сюда не пишется — он не попытка отправки, а решение человека,
    и живёт в журнале действий.

    `reason` — короткий код, а не ответ Telegram: ответ может содержать
    эхо запроса, то есть текст уведомления целиком. Расшифровка кодов
    в понятную фразу — дело интерфейса.

    У строк, созданных до появления таблицы, истории нет и не появится.
    Придумывать им времена попыток нельзя: интерфейс обязан сказать,
    что история велась не всегда.
    """

    notification = models.ForeignKey(
        Notification,
        # RESTRICT, как и везде в этой схеме: попытка — часть истории
        # уведомления, и удаление строки, за которой стоит отправка,
        # обязано отклоняться самой базой, а не аккуратностью кода.
        on_delete=models.PROTECT,
        db_column="notification_id",
        db_index=False,
        related_name="attempt_log",
    )
    # Номер попытки в пределах уведомления. Ручной повтор обнуляет
    # `attempts` у строки, поэтому нумерация здесь своя, сквозная:
    # иначе после повтора история начиналась бы с единицы поверх старой.
    number = models.IntegerField()
    attempted_at = models.DateTimeField()
    outcome = models.CharField(
        max_length=20, choices=choices(NOTIFICATION_ATTEMPT_OUTCOMES)
    )
    reason = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        db_table = "notification_attempts"
        verbose_name = "попытка отправки"
        verbose_name_plural = "попытки отправки"
        constraints = [
            status_check(
                "outcome",
                NOTIFICATION_ATTEMPT_OUTCOMES,
                "ck_notification_attempts_outcome",
            ),
            raw_check("number >= 1", "ck_notification_attempts_number_positive"),
            raw_check(
                "outcome <> 'SENT' OR reason IS NULL",
                "ck_notification_attempts_sent_has_no_reason",
            ),
        ]
        indexes = [
            models.Index(
                fields=["notification", "attempted_at"],
                name="ix_notification_attempts_row",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.notification_id} #{self.number} {self.outcome}"


class FeedRead(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Отметка «прочитано» ОДНОГО пользователя об одном событии ленты.

    Лента кадровика (`feed.py`) не хранится: она собирается из заявок,
    сессий, обращений и очереди отправки. Хранить нечего — кроме того,
    что нельзя вывести из данных: кто из кадровиков это событие уже
    видел. Поэтому таблица одна и маленькая.

    Ключ события составной — вид и запись-источник, а не выдуманный
    идентификатор: только по паре можно вернуться к строке, из которой
    событие собрано, и только так отметка переживает пересборку ленты.

    Прочтение у каждого своё: уникальность по (пользователь, вид,
    запись). Кадровик, прочитавший заявку, не отмечает её прочитанной
    для соседнего кадровика — они разбирают очередь вдвоём.
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        db_column="user_id",
        db_index=False,
        related_name="+",
    )
    event_type = models.CharField(max_length=40)
    entity_id = models.UUIDField()
    read_at = models.DateTimeField(db_default=TransactionNow())

    class Meta:
        db_table = "notification_feed_reads"
        verbose_name = "прочтение события ленты"
        verbose_name_plural = "прочтения событий ленты"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "event_type", "entity_id"],
                name="uq_notification_feed_reads_event",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"],
                name="ix_notification_feed_reads_org",
            ),
            models.Index(
                fields=["user", "event_type"],
                name="ix_notification_feed_reads_user",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.event_type}:{self.entity_id}"
