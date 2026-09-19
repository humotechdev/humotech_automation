"""QR-точки офиса и сессии показа кода на экране.

Точка — физическое место сканирования (вход, проходная, этаж). Именно она,
а не клиент, определяет офис отметки: сервер знает, к какому офису относится
точка, и подменить это данными запроса нельзя.
"""

from __future__ import annotations

from django.db import models

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    QR_DIRECTION_MODES,
    QR_DISPLAY_DEVICE_STATUSES,
    QR_DISPLAY_SESSION_STATUSES,
    QR_MODES,
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


class OfficeQrPoint(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        related_name="qr_points",
    )
    code = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    direction_mode = models.CharField(
        max_length=20, choices=choices(QR_DIRECTION_MODES)
    )
    qr_mode = models.CharField(
        max_length=20, choices=choices(QR_MODES), db_default="ROTATING"
    )
    # Хеш статического токена — по нему узнаётся отсканированный код.
    static_token_hash = models.TextField(null=True, blank=True)
    # Сам токен наклейки.
    #
    # Раньше его не хранили вовсе, и код показывался ровно один раз, при
    # выпуске. На деле это значило: потерял картинку — меняй код и бегай
    # переклеивать наклейку у каждой двери. Кадровик должен уметь
    # открыть и распечатать код офиса в любой день.
    #
    # Что это даёт тому, кто добрался до базы: ровно то же, что снимок
    # наклейки на стене. Отметку по печатному коду сервер принимает
    # только с координатами внутри радиуса офиса, поэтому знание кода
    # само по себе отметиться из дома не позволяет.
    #
    # У точек, выпущенных до этой колонки, здесь пусто: восстановить
    # прежний код неоткуда, его заменяют новым.
    static_token = models.TextField(null=True, blank=True)
    rotation_seconds = models.IntegerField(null=True, blank=True)
    token_version = models.IntegerField(db_default=1)
    require_geolocation = models.BooleanField(db_default=False)
    require_office_network = models.BooleanField(db_default=False)
    allowed_location_accuracy_m = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(db_default=True)
    # Для HR: где висит точка и зачем она. На проверку отметки не влияет.
    description = models.TextField(null=True, blank=True)
    # Кто завёл точку. SET NULL: учётную запись HR можно отключить, а
    # точка у двери от этого работать не перестаёт.
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="created_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    # Когда последний раз перевыпускали код. `token_version` отвечает на
    # «сколько раз», а на «когда» — только эта колонка.
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "office_qr_points"
        verbose_name = "QR-точка"
        verbose_name_plural = "QR-точки"
        constraints = [
            models.UniqueConstraint(
                fields=["office", "code"], name="uq_office_qr_points_office_code"
            ),
            status_check(
                "direction_mode", QR_DIRECTION_MODES,
                "ck_office_qr_points_direction_mode",
            ),
            status_check("qr_mode", QR_MODES, "ck_office_qr_points_qr_mode"),
            # Режим определяет, какое поле обязательно: у статического кода —
            # хеш токена, у меняющегося — период смены.
            raw_check(
                "(qr_mode = 'STATIC' AND static_token_hash IS NOT NULL) "
                "OR (qr_mode = 'ROTATING' AND rotation_seconds IS NOT NULL)",
                "ck_office_qr_points_mode_requires_fields",
            ),
            raw_check(
                "rotation_seconds IS NULL "
                "OR (rotation_seconds >= 15 AND rotation_seconds <= 300)",
                "ck_office_qr_points_rotation_seconds_range",
            ),
            raw_check("token_version > 0", "ck_office_qr_points_token_version_positive"),
            raw_check(
                "allowed_location_accuracy_m IS NULL "
                "OR allowed_location_accuracy_m > 0",
                "ck_office_qr_points_accuracy_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_office_qr_points_organization_id"
            ),
            models.Index(fields=["office"], name="ix_office_qr_points_office_id"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class QrDisplayDevice(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Экран в офисе, которому разрешено запрашивать коды.

    Экран привязан к одной QR-точке, и офис берётся из неё. Frontend не
    передаёт ни офис, ни точку: подменить можно только то, что где-то
    принимается.

    Живёт в два шага. Сначала HR заводит устройство и получает одноразовый
    код сопряжения — он показывается ровно один раз, в базе от него остаётся
    только хеш. Экран предъявляет этот код, получает собственный долгий
    credential и переходит в ACTIVE; хеш кода сопряжения при этом стирается,
    поэтому второй раз тем же кодом сопрячься нельзя.
    """

    qr_point = models.ForeignKey(
        OfficeQrPoint,
        on_delete=models.PROTECT,
        db_column="qr_point_id",
        db_index=False,
        related_name="display_devices",
    )
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20, choices=choices(QR_DISPLAY_DEVICE_STATUSES)
    )
    # Только хеши. Ни код сопряжения, ни credential в базе не лежат:
    # чтения базы недостаточно, чтобы начать выдавать коды.
    pairing_secret_hash = models.CharField(max_length=64, null=True, blank=True)
    pairing_expires_at = models.DateTimeField(null=True, blank=True)
    paired_at = models.DateTimeField(null=True, blank=True)
    credential_hash = models.CharField(max_length=64, null=True, blank=True)
    credential_issued_at = models.DateTimeField(null=True, blank=True)
    credential_expires_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
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
        db_table = "qr_display_devices"
        verbose_name = "экран показа QR"
        verbose_name_plural = "экраны показа QR"
        constraints = [
            status_check(
                "status", QR_DISPLAY_DEVICE_STATUSES, "ck_qr_display_devices_status"
            ),
            # Состояние и наличие секретов должны совпадать, иначе устройство
            # оказывается ACTIVE без credential — то есть недоступно, но
            # выглядит рабочим.
            raw_check(
                "status <> 'PENDING' OR pairing_secret_hash IS NOT NULL",
                "ck_qr_display_devices_pending_has_secret",
            ),
            raw_check(
                "status <> 'ACTIVE' OR credential_hash IS NOT NULL",
                "ck_qr_display_devices_active_has_credential",
            ),
            # Отозванное устройство не должно сохранять рабочий credential:
            # иначе отзыв виден в интерфейсе, но не в проверке доступа.
            raw_check(
                "status <> 'REVOKED' "
                "OR (revoked_at IS NOT NULL AND credential_hash IS NULL)",
                "ck_qr_display_devices_revoked_is_disarmed",
            ),
            models.UniqueConstraint(
                fields=["credential_hash"],
                condition=models.Q(credential_hash__isnull=False),
                name="uq_qr_display_devices_credential",
            ),
            models.UniqueConstraint(
                fields=["pairing_secret_hash"],
                condition=models.Q(pairing_secret_hash__isnull=False),
                name="uq_qr_display_devices_pairing_secret",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_qr_display_devices_organization_id"
            ),
            models.Index(
                fields=["qr_point"], name="ix_qr_display_devices_qr_point_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"


class QrDisplaySession(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Сессия показа меняющегося QR на экране терминала."""

    qr_point = models.ForeignKey(
        OfficeQrPoint,
        on_delete=models.PROTECT,
        db_column="qr_point_id",
        db_index=False,
        related_name="display_sessions",
    )
    device = models.ForeignKey(
        QrDisplayDevice,
        on_delete=models.PROTECT,
        db_column="device_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="sessions",
    )
    # Только хеш идентификатора экрана: сам идентификатор не хранится.
    display_identifier_hash = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=choices(QR_DISPLAY_SESSION_STATUSES)
    )
    started_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    started_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="started_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "qr_display_sessions"
        verbose_name = "сессия показа QR"
        verbose_name_plural = "сессии показа QR"
        constraints = [
            status_check(
                "status", QR_DISPLAY_SESSION_STATUSES,
                "ck_qr_display_sessions_status",
            ),
            raw_check(
                "ended_at IS NULL OR ended_at >= started_at",
                "ck_qr_display_sessions_end_after_start",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_qr_display_sessions_organization_id"
            ),
            models.Index(
                fields=["qr_point"], name="ix_qr_display_sessions_qr_point_id"
            ),
            models.Index(fields=["device"], name="ix_qr_display_sessions_device_id"),
        ]

    def __str__(self) -> str:
        return f"{self.qr_point_id} {self.status}"
