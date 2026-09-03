"""QR-точки офиса и сессии показа rotating QR на экране.

Главное правило: QR принадлежит ТОЧКЕ ОФИСА, а не сотруднику. Офис определяется
сервером через `office_qr_points.office_id`; `office_id`, присланный клиентом,
не используется никогда.

Сырой QR-токен в базе не хранится:
  * STATIC   — храним только hash (`static_token_hash`);
  * ROTATING — не храним вовсе, сервер выдаёт короткоживущий подписанный токен
               (qr_point_id, display_session_id, direction, issued_at, expires_at,
               nonce, signature) и проверяет подпись при сканировании.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import (
    ArchivableMixin,
    Base,
    CreatedAtMixin,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from src.core.database.enums import (
    QR_DIRECTION_MODES,
    QR_DISPLAY_SESSION_STATUSES,
    QR_MODES,
    in_check,
)

if TYPE_CHECKING:
    from src.modules.offices.models import Office


class OfficeQrPoint(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin, ArchivableMixin, Base
):
    """Физическая точка сканирования: главный вход, служебный вход и т.п."""

    __tablename__ = "office_qr_points"

    office_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # ENTRY / EXIT — точка фиксирует только вход или только выход;
    # BOTH — направление определяет сервер по последней открытой сессии
    direction_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    qr_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="ROTATING"
    )
    # только для STATIC: hash токена, сам токен не хранится
    static_token_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # только для ROTATING: время жизни одного QR, по умолчанию 45 секунд
    rotation_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # инкрементируется при перевыпуске — все старые токены сразу недействительны
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    require_geolocation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    require_office_network: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    allowed_location_accuracy_m: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    office: Mapped["Office"] = relationship(back_populates="qr_points")
    display_sessions: Mapped[list["QrDisplaySession"]] = relationship(
        back_populates="qr_point"
    )

    __table_args__ = (
        UniqueConstraint("office_id", "code", name="uq_office_qr_points_office_code"),
        in_check("direction_mode", QR_DIRECTION_MODES, "direction_mode"),
        in_check("qr_mode", QR_MODES, "qr_mode"),
        # у STATIC обязателен hash, у ROTATING — время жизни токена
        CheckConstraint(
            "(qr_mode = 'STATIC'   AND static_token_hash IS NOT NULL) OR "
            "(qr_mode = 'ROTATING' AND rotation_seconds IS NOT NULL)",
            name="mode_requires_fields",
        ),
        CheckConstraint(
            "rotation_seconds IS NULL OR rotation_seconds BETWEEN 15 AND 300",
            name="rotation_seconds_range",
        ),
        CheckConstraint("token_version > 0", name="token_version_positive"),
        CheckConstraint(
            "allowed_location_accuracy_m IS NULL OR allowed_location_accuracy_m > 0",
            name="accuracy_positive",
        ),
    )


class QrDisplaySession(
    UUIDPrimaryKeyMixin, OrganizationScopedMixin, CreatedAtMixin, Base
):
    """Сеанс показа rotating QR на конкретном экране офиса.

    Отдельной строки на каждый выпущенный QR НЕ создаётся — это была бы таблица
    на миллионы строк в сутки. Строка появляется на весь сеанс работы экрана,
    а сами токены живут в подписи и в `attendance_events.qr_nonce_hash`.
    """

    __tablename__ = "qr_display_sessions"

    qr_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("office_qr_points.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # hash идентификатора экрана: сам идентификатор в открытом виде не храним
    display_identifier_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    qr_point: Mapped["OfficeQrPoint"] = relationship(back_populates="display_sessions")

    __table_args__ = (
        in_check("status", QR_DISPLAY_SESSION_STATUSES, "status"),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="end_after_start"
        ),
    )
