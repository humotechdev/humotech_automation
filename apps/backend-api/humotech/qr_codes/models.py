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
    # Только хеш статического токена: сам токен в базе не лежит.
    static_token_hash = models.TextField(null=True, blank=True)
    rotation_seconds = models.IntegerField(null=True, blank=True)
    token_version = models.IntegerField(db_default=1)
    require_geolocation = models.BooleanField(db_default=False)
    require_office_network = models.BooleanField(db_default=False)
    allowed_location_accuracy_m = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(db_default=True)

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


class QrDisplaySession(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Сессия показа меняющегося QR на экране терминала."""

    qr_point = models.ForeignKey(
        OfficeQrPoint,
        on_delete=models.PROTECT,
        db_column="qr_point_id",
        db_index=False,
        related_name="display_sessions",
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
        ]

    def __str__(self) -> str:
        return f"{self.qr_point_id} {self.status}"
