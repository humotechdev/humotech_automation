"""Отметки о входе и выходе, рабочие сессии и заявки на исправление."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    ATTENDANCE_EVENT_TYPES,
    ATTENDANCE_SESSION_STATUSES,
    ATTENDANCE_SOURCES,
    CORRECTION_REQUEST_STATUSES,
    DAY_NOTICE_KINDS,
    VERIFICATION_STATUSES,
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


class AttendanceEvent(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Неизменяемое событие сканирования. Без updated_at — строка не меняется."""

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="attendance_events",
    )
    # исторический snapshot: офис, определённый сервером через QR-точку в момент
    # события. Если точку потом перенесут в другой офис — история не поедет.
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        related_name="attendance_events",
    )
    qr_point = models.ForeignKey(
        "qr_codes.OfficeQrPoint",
        on_delete=models.PROTECT,
        db_column="qr_point_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    qr_display_session = models.ForeignKey(
        "qr_codes.QrDisplaySession",
        on_delete=models.PROTECT,
        db_column="qr_display_session_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    employee_device = models.ForeignKey(
        "devices.EmployeeDevice",
        on_delete=models.PROTECT,
        db_column="employee_device_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    event_type = models.CharField(
        max_length=20, choices=choices(ATTENDANCE_EVENT_TYPES)
    )
    source = models.CharField(max_length=20, choices=choices(ATTENDANCE_SOURCES))
    # ставит ТОЛЬКО сервер: клиент не может прислать готовый результат проверки
    verification_status = models.CharField(
        max_length=20, choices=choices(VERIFICATION_STATUSES)
    )
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(db_default=TransactionNow())

    # одноразовость QR: nonce хранится хешем, повтор ловится уникальным индексом
    qr_nonce_hash = models.TextField(null=True, blank=True)
    qr_issued_at = models.DateTimeField(null=True, blank=True)
    qr_expires_at = models.DateTimeField(null=True, blank=True)

    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    location_accuracy_m = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    inside_geofence = models.BooleanField(null=True, blank=True)
    # Расстояние от сотрудника до точки офиса в момент попытки, в метрах.
    # Пусто, если сравнивать было не с чем: координат не прислали или у
    # офиса их нет. Хранится ради ответа на «насколько далеко он был»,
    # а не только «внутри ли».
    distance_m = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    inside_office_network = models.BooleanField(null=True, blank=True)

    # идемпотентность повторной отправки с клиента при плохой связи
    client_event_id = models.CharField(max_length=255, null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, null=True, blank=True)
    event_metadata = models.JSONField(db_column="metadata", null=True, blank=True)

    class Meta:
        db_table = "attendance_events"
        verbose_name = "отметка"
        verbose_name_plural = "отметки"
        constraints = [
            status_check(
                "event_type", ATTENDANCE_EVENT_TYPES, "ck_attendance_events_event_type"
            ),
            status_check("source", ATTENDANCE_SOURCES, "ck_attendance_events_source"),
            status_check(
                "verification_status", VERIFICATION_STATUSES,
                "ck_attendance_events_verification_status",
            ),
            # отметка по QR обязана ссылаться на QR-точку — иначе офис не определить
            raw_check(
                "source <> 'QR' OR qr_point_id IS NOT NULL",
                "ck_attendance_events_qr_requires_point",
            ),
            raw_check(
                "qr_expires_at IS NULL OR qr_issued_at IS NULL "
                "OR qr_expires_at > qr_issued_at",
                "ck_attendance_events_qr_expiry_after_issue",
            ),
            raw_check(
                "distance_m IS NULL OR distance_m >= 0",
                "ck_attendance_events_distance_non_negative",
            ),
            # Повторное сканирование одного и того же кода не создаёт второй
            # отметки. Уникальность только среди ПРИНЯТЫХ: отклонённая попытка
            # не должна навсегда занимать nonce.
            models.UniqueConstraint(
                fields=["employee", "qr_nonce_hash", "event_type"],
                condition=Q(qr_nonce_hash__isnull=False,
                            verification_status="ACCEPTED"),
                name="uq_attendance_events_nonce",
            ),
            models.UniqueConstraint(
                fields=["employee", "client_event_id"],
                condition=Q(client_event_id__isnull=False),
                name="uq_attendance_events_client_event",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_attendance_events_organization_id"
            ),
            models.Index(
                fields=["employee", "-occurred_at"],
                name="ix_attendance_events_employee_time",
            ),
            models.Index(
                fields=["office", "-occurred_at"],
                name="ix_attendance_events_office_time",
            ),
            models.Index(
                fields=["qr_point", "-occurred_at"],
                name="ix_attendance_events_qr_point_time",
            ),
            models.Index(
                fields=["verification_status", "-occurred_at"],
                name="ix_attendance_events_status_time",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.occurred_at:%Y-%m-%d %H:%M}"


class AttendanceSession(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Интервал нахождения в офисе, посчитанный бэкендом из пары событий."""

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="attendance_sessions",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        related_name="attendance_sessions",
    )
    entry_event = models.ForeignKey(
        AttendanceEvent,
        on_delete=models.PROTECT,
        db_column="entry_event_id",
        db_index=False,
        related_name="+",
    )
    exit_event = models.ForeignKey(
        AttendanceEvent,
        on_delete=models.PROTECT,
        db_column="exit_event_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    # считает бэкенд, не клиент
    duration_seconds = models.IntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=choices(ATTENDANCE_SESSION_STATUSES)
    )
    calculated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "attendance_sessions"
        verbose_name = "рабочая сессия"
        verbose_name_plural = "рабочие сессии"
        constraints = [
            status_check(
                "status", ATTENDANCE_SESSION_STATUSES, "ck_attendance_sessions_status"
            ),
            raw_check(
                "ended_at IS NULL OR ended_at >= started_at",
                "ck_attendance_sessions_end_after_start",
            ),
            raw_check(
                "duration_seconds IS NULL OR duration_seconds >= 0",
                "ck_attendance_sessions_duration_non_negative",
            ),
            # закрытая сессия обязана иметь и время выхода, и событие выхода
            raw_check(
                "status <> 'CLOSED' "
                "OR (ended_at IS NOT NULL AND exit_event_id IS NOT NULL)",
                "ck_attendance_sessions_closed_has_exit",
            ),
            # У сотрудника не может быть двух открытых сессий одновременно.
            models.UniqueConstraint(
                fields=["employee"],
                condition=Q(status="OPEN"),
                name="uq_attendance_sessions_one_open",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_attendance_sessions_organization_id"
            ),
            models.Index(
                fields=["employee", "-started_at"],
                name="ix_attendance_sessions_employee_time",
            ),
            models.Index(
                fields=["office", "-started_at"],
                name="ix_attendance_sessions_office_time",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} {self.started_at:%Y-%m-%d}"


class AttendanceCorrectionRequest(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Заявка сотрудника исправить отметку: забыл отсканировать, ошибся."""

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="correction_requests",
    )
    attendance_session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.PROTECT,
        db_column="attendance_session_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="correction_requests",
    )
    requested_entry_at = models.DateTimeField(null=True, blank=True)
    requested_exit_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()
    status = models.CharField(
        max_length=20, choices=choices(CORRECTION_REQUEST_STATUSES)
    )
    submitted_at = models.DateTimeField()
    reviewed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="reviewed_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "attendance_correction_requests"
        verbose_name = "заявка на исправление отметки"
        verbose_name_plural = "заявки на исправление отметок"
        constraints = [
            status_check(
                "status", CORRECTION_REQUEST_STATUSES,
                "ck_attendance_correction_requests_status",
            ),
            # заявка обязана что-то менять
            raw_check(
                "requested_entry_at IS NOT NULL OR requested_exit_at IS NOT NULL",
                "ck_attendance_correction_requests_something_requested",
            ),
            raw_check(
                "requested_exit_at IS NULL OR requested_entry_at IS NULL "
                "OR requested_exit_at >= requested_entry_at",
                "ck_attendance_correction_requests_exit_after_entry",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"],
                name="ix_attendance_correction_requests_organization_id",
            ),
            models.Index(
                fields=["employee"],
                name="ix_attendance_correction_requests_employee_id",
            ),
        ]

    def __str__(self) -> str:
        return f"заявка {self.employee_id} ({self.status})"


class DayNotice(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Что человек сам сказал про свой день: опаздывает или не придёт.

    Это НЕ отметка и не заявка. Отметка — факт, подтверждённый кодом на
    двери; заявка на отпуск проходит согласование и меняет учёт. Сказанное
    в чате не делает ни того, ни другого: оно объясняет пустую строку в
    табеле и ничего больше.

    Отсюда два правила, которые легко нарушить:

    **«Не приду» не ставит отпуск и не открывает больничный.** Иначе
    любой человек оформлял бы себе отсутствие одной кнопкой, минуя
    согласование. Бот на этот ответ отвечает просьбой подать заявку.

    **Одна строка на человека и день.** Сказавший «опаздываю» и потом
    «не приду» не должен превращаться в две записи, из которых табель
    выберет случайную: строка обновляется, а прежнее значение видно в
    журнале аудита.
    """

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="day_notices",
    )
    #: День, о котором речь, в поясе офиса. Дата, а не момент: «сегодня»
    #: у сервера и у человека совпадает только так.
    day = models.DateField()
    kind = models.CharField(max_length=20, choices=choices(DAY_NOTICE_KINDS))
    #: Причина словами. Необязательна намеренно: требовать объяснение у
    #: того, кто стоит в пробке, — способ не получить ни объяснения, ни
    #: предупреждения.
    comment = models.TextField(null=True, blank=True)
    #: Когда человек это сказал. Отдельно от `created_at`: строку
    #: обновляют, и время первого ответа иначе потерялось бы.
    noticed_at = models.DateTimeField()

    class Meta:
        db_table = "attendance_day_notices"
        verbose_name = "сообщение сотрудника о дне"
        verbose_name_plural = "сообщения сотрудников о дне"
        constraints = [
            status_check(
                "kind", DAY_NOTICE_KINDS, "ck_attendance_day_notices_kind"
            ),
            models.UniqueConstraint(
                fields=["employee", "day"],
                name="uq_attendance_day_notices_employee_day",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "day"],
                name="ix_attendance_day_notices_org_day",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.employee_id} {self.day}"
