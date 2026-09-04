"""Экраны показа QR: заведение, сопряжение, выдача кодов, отзыв.

Экран в офисе — обычный планшет на стене. Он не человек, у него нет ни
пароля, ни роли, и просить его «войти» некому: рядом с ним никто не сидит.
Поэтому доступ устроен в два шага.

  1. **HR заводит устройство** и получает одноразовый код сопряжения. Код
     показывается ровно один раз — в базе от него остаётся только хеш.
     Устройство привязано к конкретной QR-точке, а офис берётся из точки:
     frontend офис не выбирает и выбрать не может;
  2. **экран предъявляет код** и получает собственный `credential`. Хеш
     кода сопряжения при этом стирается, поэтому второй раз тем же кодом
     сопрячься нельзя — подсмотренный код бесполезен, если экран уже
     сопряжён.

Дальше экран показывает `credential` при каждом запросе кода. Отзыв —
одно поле: `credential_hash` обнуляется, и следующий запрос экрана уже
ничего не получает.

Отдельно про то, где `credential` лежит на самом экране: в `localStorage`,
и это НЕ противоречит правилу Mini App «никогда не localStorage». Там
хранится доступ человека на устройстве, которое могут взять другие; здесь —
доступ самого устройства, которое обязано пережить перезагрузку и утренний
запуск без человека рядом. Угрозы разные, и ответы поэтому разные.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from humotech.core.errors import Conflict, NotFound, PermissionDenied
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.qr_codes.models import OfficeQrPoint, QrDisplayDevice, QrDisplaySession
from humotech.qr_codes.tokens import issue as issue_token

ENTITY_DEVICE = "qr_display_devices"

# Что попадает в журнал. Ни кода сопряжения, ни credential — даже хешей:
# `AuditTrail` вычёркивает их отдельно, но правильнее просто не собирать.
DEVICE_AUDIT_FIELDS = (
    "qr_point_id", "name", "status", "paired_at", "revoked_at",
    "credential_issued_at", "credential_expires_at",
)


@dataclass(frozen=True)
class IssuedDevice:
    """Устройство и его одноразовый код — код существует только здесь."""

    device: QrDisplayDevice
    pairing_code: str


@dataclass(frozen=True)
class PairedDevice:
    """Результат сопряжения. `credential` больше нигде не восстановим."""

    device: QrDisplayDevice
    credential: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedQr:
    """Код для показа на экране."""

    token: str
    issued_at: datetime
    expires_at: datetime
    office_name: str
    point_name: str
    direction_mode: str


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class QrDisplayService(BaseService):
    """Управление экранами. HR-часть требует прав, экранная — credential."""

    # ------------------------------------------------------------ для HR

    def create_device(
        self, actor: Actor, *, qr_point_id: uuid.UUID, name: str
    ) -> IssuedDevice:
        self.access.require(actor, "qr_points.manage")
        point = self._require_point(actor, qr_point_id)
        return self.create_device_for_point(point, name=name, actor=actor)

    def reissue_pairing(self, actor: Actor, device_id: uuid.UUID) -> IssuedDevice:
        self.access.require(actor, "qr_points.manage")
        device = self._require_device(actor, device_id)
        return self.reissue_pairing_for_device(device, actor=actor)

    def revoke_device(self, actor: Actor, device_id: uuid.UUID) -> QrDisplayDevice:
        self.access.require(actor, "qr_points.manage")
        device = self._require_device(actor, device_id)
        return self.revoke_the_device(device, actor=actor)

    # --- те же операции, но без пользователя CRM ------------------------
    #
    # Экраны разворачивают из командной строки: интерфейса для них ещё нет,
    # а офис открывается уже сейчас. Методы ниже — те же самые операции,
    # только без проверки прав, и звать их можно лишь оттуда, где проверять
    # нечего: у management-команды нет ни сессии, ни роли.
    #
    # Смысл разделения — одна реализация на операцию. Команда с собственной
    # копией логики через полгода расходится с сервисом, и обнаруживается
    # это на работающем офисе.

    def create_device_for_point(
        self, point: OfficeQrPoint, *, name: str, actor: Actor | None = None
    ) -> IssuedDevice:
        code = secrets.token_urlsafe(24)
        now = timezone.now()
        with self.atomic():
            device = QrDisplayDevice.objects.create(
                organization_id=point.organization_id,
                qr_point=point,
                name=name,
                status="PENDING",
                pairing_secret_hash=_hash(code),
                pairing_expires_at=now
                + timedelta(seconds=settings.QR["DISPLAY_PAIRING_TTL_SECONDS"]),
                created_by_user_id=actor.user_id if actor else None,
            )
            self._record(
                actor,
                device,
                action="qr.display.device.create",
                after=snapshot(device, DEVICE_AUDIT_FIELDS),
            )
        return IssuedDevice(device=device, pairing_code=code)

    def reissue_pairing_for_device(
        self, device: QrDisplayDevice, *, actor: Actor | None = None
    ) -> IssuedDevice:
        """Новый код сопряжения — например, если экран заменили.

        Прежний credential при этом снимается: иначе старое устройство
        продолжало бы получать коды параллельно с новым.
        """
        code = secrets.token_urlsafe(24)
        now = timezone.now()
        with self.atomic():
            before = snapshot(device, DEVICE_AUDIT_FIELDS)
            device.status = "PENDING"
            device.pairing_secret_hash = _hash(code)
            device.pairing_expires_at = now + timedelta(
                seconds=settings.QR["DISPLAY_PAIRING_TTL_SECONDS"]
            )
            device.credential_hash = None
            device.credential_issued_at = None
            device.credential_expires_at = None
            device.revoked_at = None
            device.save()
            self._close_sessions(device, now)
            self._record(
                actor,
                device,
                action="qr.display.device.reissue",
                before=before,
                after=snapshot(device, DEVICE_AUDIT_FIELDS),
            )
        return IssuedDevice(device=device, pairing_code=code)

    def revoke_the_device(
        self, device: QrDisplayDevice, *, actor: Actor | None = None
    ) -> QrDisplayDevice:
        if device.status == "REVOKED":
            raise Conflict("Экран уже отозван")

        now = timezone.now()
        with self.atomic():
            before = snapshot(device, DEVICE_AUDIT_FIELDS)
            device.status = "REVOKED"
            device.revoked_at = now
            # Обязательно вместе со статусом: ограничение в базе не даст
            # оставить рабочий credential у отозванного экрана, и это
            # правильно — иначе отзыв был бы виден в интерфейсе, но не
            # в проверке доступа.
            device.credential_hash = None
            device.pairing_secret_hash = None
            device.save()
            self._close_sessions(device, now)
            self._record(
                actor,
                device,
                action="qr.display.device.revoke",
                before=before,
                after=snapshot(device, DEVICE_AUDIT_FIELDS),
            )
        return device

    def list_devices(self, actor: Actor, *, qr_point_id: uuid.UUID | None = None):
        self.access.require(actor, "qr_points.read")
        queryset = QrDisplayDevice.objects.filter(
            organization_id=actor.organization_id
        ).select_related("qr_point", "qr_point__office")
        if qr_point_id is not None:
            queryset = queryset.filter(qr_point_id=qr_point_id)
        return queryset.order_by("qr_point__code", "name")

    # -------------------------------------------------------- для экрана

    def pair(self, pairing_code: str, *, now: datetime | None = None) -> PairedDevice:
        """Обмен одноразового кода на собственный credential экрана."""
        moment = now or timezone.now()
        code_hash = _hash(pairing_code)

        with transaction.atomic():
            device = (
                QrDisplayDevice.objects.select_for_update()
                .select_related("qr_point", "qr_point__office")
                .filter(pairing_secret_hash=code_hash)
                .first()
            )
            # Несуществующий и уже использованный код неразличимы намеренно.
            if device is None:
                raise PermissionDenied("Код сопряжения недействителен")
            if device.status == "REVOKED":
                raise PermissionDenied("Код сопряжения недействителен")
            if (
                device.pairing_expires_at is not None
                and device.pairing_expires_at <= moment
            ):
                raise PermissionDenied("Код сопряжения недействителен")

            credential = secrets.token_urlsafe(32)
            device.status = "ACTIVE"
            device.credential_hash = _hash(credential)
            device.credential_issued_at = moment
            device.credential_expires_at = moment + timedelta(
                seconds=settings.QR["DISPLAY_CREDENTIAL_TTL_SECONDS"]
            )
            device.paired_at = moment
            # Код сопряжения гасится: подсмотренный код бесполезен, если
            # экран уже сопряжён.
            device.pairing_secret_hash = None
            device.pairing_expires_at = None
            device.last_seen_at = moment
            device.save()

            self.audit.record_system(
                organization_id=device.organization_id,
                action="qr.display.device.pair",
                entity_type=ENTITY_DEVICE,
                entity_id=device.id,
                after=snapshot(device, DEVICE_AUDIT_FIELDS),
            )

        return PairedDevice(
            device=device,
            credential=credential,
            expires_at=device.credential_expires_at,
        )

    def authenticate_device(
        self, credential: str, *, now: datetime | None = None
    ) -> QrDisplayDevice | None:
        """Проверка credential на КАЖДОМ запросе кода.

        Перечитывается заново, а не кэшируется: отзыв экрана обязан
        действовать сразу, а не с истечением срока.
        """
        moment = now or timezone.now()
        device = (
            QrDisplayDevice.objects.select_related(
                "qr_point", "qr_point__office", "organization"
            )
            .filter(credential_hash=_hash(credential), status="ACTIVE")
            .first()
        )
        if device is None:
            return None
        if (
            device.credential_expires_at is not None
            and device.credential_expires_at <= moment
        ):
            return None
        if device.organization.status != "ACTIVE":
            return None
        return device

    def issue_qr(
        self, device: QrDisplayDevice, *, now: datetime | None = None
    ) -> IssuedQr:
        """Очередной код для экрана.

        Точка берётся из устройства, офис — из точки. Ни то ни другое
        не приходит из запроса: экран не выбирает, что показывать.
        """
        moment = now or timezone.now()
        point = device.qr_point
        if not point.is_active or point.archived_at is not None:
            raise Conflict("Точка отметки выключена")

        ttl = settings.QR["TOKEN_TTL_SECONDS"]
        token, payload = issue_token(
            organization_id=point.organization_id,
            office_id=point.office_id,
            qr_point_id=point.id,
            direction_mode=point.direction_mode,
            issued_at=moment,
            ttl_seconds=ttl,
        )

        # Отметка «экран на связи» и сессия показа — материал для разбора:
        # по ним видно, какой экран показал код, по которому прошла отметка.
        QrDisplayDevice.objects.filter(id=device.id).update(last_seen_at=moment)
        self._touch_session(device, moment)

        return IssuedQr(
            token=token,
            issued_at=payload.issued_at,
            expires_at=payload.expires_at,
            office_name=point.office.name,
            point_name=point.name,
            direction_mode=point.direction_mode,
        )

    # ------------------------------------------------------------- внутри

    def _record(self, actor, device, *, action, before=None, after=None) -> None:
        """Одна запись в журнал, кем бы ни было совершено действие.

        Запуск из командной строки — не повод оставить выдачу доступа
        неучтённой: `actor_user_id` останется пустым, но сама запись будет.
        """
        if actor is not None:
            self.audit.record(
                actor,
                action=action,
                entity_type=ENTITY_DEVICE,
                entity_id=device.id,
                before=before,
                after=after,
            )
            return
        self.audit.record_system(
            organization_id=device.organization_id,
            action=action,
            entity_type=ENTITY_DEVICE,
            entity_id=device.id,
            before=before,
            after=after,
        )

    def _touch_session(self, device: QrDisplayDevice, moment: datetime) -> None:
        session = QrDisplaySession.objects.filter(
            device_id=device.id, status="ACTIVE"
        ).first()
        if session is None:
            QrDisplaySession.objects.create(
                organization_id=device.organization_id,
                qr_point_id=device.qr_point_id,
                device_id=device.id,
                status="ACTIVE",
                started_at=moment,
                last_seen_at=moment,
            )
            return
        QrDisplaySession.objects.filter(id=session.id).update(last_seen_at=moment)

    def _close_sessions(self, device: QrDisplayDevice, moment: datetime) -> None:
        QrDisplaySession.objects.filter(
            device_id=device.id, status="ACTIVE"
        ).update(status="CLOSED", ended_at=moment)

    def _require_point(self, actor: Actor, point_id: uuid.UUID) -> OfficeQrPoint:
        point = (
            OfficeQrPoint.objects.select_related("office")
            .filter(id=point_id, organization_id=actor.organization_id)
            .first()
        )
        if point is None:
            # Чужая организация отвечает так же, как отсутствие записи.
            raise NotFound("Точка отметки не найдена")
        return point

    def _require_device(self, actor: Actor, device_id: uuid.UUID) -> QrDisplayDevice:
        device = (
            QrDisplayDevice.objects.select_related("qr_point", "qr_point__office")
            .filter(id=device_id, organization_id=actor.organization_id)
            .first()
        )
        if device is None:
            raise NotFound("Экран не найден")
        return device


__all__ = [
    "IssuedDevice",
    "IssuedQr",
    "PairedDevice",
    "QrDisplayService",
]
