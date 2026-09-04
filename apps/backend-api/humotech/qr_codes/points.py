"""Точки отметки: справочник, настройки и статический секрет.

Точка — это дверь. У неё есть офис, направление (вход, выход или оба) и
режим: `ROTATING` — код на экране меняется каждые несколько секунд,
`STATIC` — наклейка с неизменным кодом.

Про секрет статической точки здесь одно правило, и оно не обсуждается:
**показать его можно ровно один раз, в ответе на выпуск.** В базе лежит
только хеш. Забыли записать — перевыпускайте; прежний перестанет работать.
Отдавать секрет повторно значило бы хранить его в открытом виде, а
наклейка с кодом висит на стене там, где ходят люди: тот, кто однажды
сфотографировал её, не должен получать возможность отметиться из дома
до конца времён.

Точка не удаляется, а выключается. На неё ссылаются отметки, и удаление
означало бы потерю ответа на вопрос «где именно человек приложил
пропуск». Внешние ключи объявлены с `ON DELETE RESTRICT`, так что база
и не позволит.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from humotech.core.enums import QR_DIRECTION_MODES, QR_MODES
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_code, clean_text
from humotech.qr_codes.models import OfficeQrPoint

AUDITED_FIELDS = (
    "code",
    "name",
    "direction_mode",
    "qr_mode",
    "rotation_seconds",
    "require_geolocation",
    "require_office_network",
    "allowed_location_accuracy_m",
    "is_active",
    "token_version",
)

# Разумные границы смены кода на экране. Меньше пяти секунд — человек
# не успевает навести камеру; больше пяти минут — снятый на телефон код
# слишком долго остаётся рабочим.
MIN_ROTATION_SECONDS = 5
MAX_ROTATION_SECONDS = 300


@dataclass(frozen=True)
class IssuedPoint:
    """Точка и её секрет — секрет присутствует ТОЛЬКО здесь.

    Сериализатор списка и карточки этого поля не знает вовсе: так его
    нельзя случайно отдать вторым запросом.
    """

    point: OfficeQrPoint
    static_token: str | None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class QrPointService(BaseService):
    """Требует `qr_points.read` для чтения и `qr_points.manage` для правок."""

    # ------------------------------------------------------------------ чтение

    def list(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        search: str | None = None,
        is_active: bool | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "qr_points.read")

        queryset = OfficeQrPoint.objects.filter(
            organization_id=actor.organization_id
        ).select_related("office", "office__region")

        if office_id:
            self.access.require_office(actor, office_id)
            queryset = queryset.filter(office_id=office_id)
        elif region_id:
            self.access.require_region(actor, region_id)
            queryset = queryset.filter(office__region_id=region_id)
        else:
            visible = self.access.visible_office_ids(actor)
            if visible is not None:
                queryset = queryset.filter(office_id__in=visible)

        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(name__icontains=pattern) | queryset.filter(
                code__icontains=pattern
            )
        return paginate(queryset, limit=limit, cursor=cursor)

    def get(self, actor: Actor, point_id: uuid.UUID) -> OfficeQrPoint:
        self.access.require(actor, "qr_points.read")
        return self._require_point(actor, point_id)

    # -------------------------------------------------------------- изменения

    def create(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID,
        code: str,
        name: str,
        direction_mode: str = "BOTH",
        qr_mode: str = "ROTATING",
        rotation_seconds: int | None = None,
        require_geolocation: bool = False,
        require_office_network: bool = False,
        allowed_location_accuracy_m: int | None = None,
    ) -> IssuedPoint:
        """Новая точка. Для `STATIC` сразу выпускается секрет.

        Секрет возвращается здесь и больше нигде и никогда.
        """
        self.access.require(actor, "qr_points.manage")
        office = self.access.require_office(actor, office_id)

        self._validate_mode(qr_mode, direction_mode, rotation_seconds)

        token = secrets.token_urlsafe(32) if qr_mode == "STATIC" else None
        with self.atomic():
            point = OfficeQrPoint.objects.create(
                organization_id=actor.organization_id,
                office=office,
                code=clean_code(code, field="code"),
                name=clean_text(name, field="name", max_length=255),
                direction_mode=direction_mode,
                qr_mode=qr_mode,
                rotation_seconds=(
                    rotation_seconds if qr_mode == "ROTATING" else None
                ),
                static_token_hash=_hash(token) if token else None,
                require_geolocation=require_geolocation,
                require_office_network=require_office_network,
                allowed_location_accuracy_m=allowed_location_accuracy_m,
                is_active=True,
            )
            self.audit.record(
                actor,
                action="qr.point.create",
                entity_type="office_qr_points",
                entity_id=point.id,
                before=None,
                # `static_token_hash` не попадает в журнал: в нём нет
                # смысла, а место для утечки он создаёт.
                after=snapshot(point, AUDITED_FIELDS),
            )
        return IssuedPoint(point=point, static_token=token)

    def update(
        self,
        actor: Actor,
        point_id: uuid.UUID,
        *,
        name: str | None = None,
        direction_mode: str | None = None,
        rotation_seconds: int | None = None,
        require_geolocation: bool | None = None,
        require_office_network: bool | None = None,
        allowed_location_accuracy_m: int | None = None,
    ) -> OfficeQrPoint:
        """Название, направление и требования к отметке.

        Режим (`qr_mode`) здесь не меняется. Переключение статической
        точки в поворотную и обратно — это выпуск или отзыв секрета,
        то есть другая операция с другими последствиями; делать её
        незаметной правкой поля нельзя.
        """
        self.access.require(actor, "qr_points.manage")
        point = self._require_point(actor, point_id)
        before = snapshot(point, AUDITED_FIELDS)

        if name is not None:
            point.name = clean_text(name, field="name", max_length=255)
        if direction_mode is not None:
            if direction_mode not in QR_DIRECTION_MODES:
                raise ValidationFailed(
                    "Неизвестное направление точки",
                    details={"direction_mode": direction_mode,
                             "allowed": list(QR_DIRECTION_MODES)},
                )
            point.direction_mode = direction_mode
        if rotation_seconds is not None:
            if point.qr_mode != "ROTATING":
                raise ValidationFailed(
                    "Период смены кода есть только у поворотной точки",
                    details={"qr_mode": point.qr_mode},
                )
            _validate_rotation(rotation_seconds)
            point.rotation_seconds = rotation_seconds
        if require_geolocation is not None:
            point.require_geolocation = require_geolocation
        if require_office_network is not None:
            point.require_office_network = require_office_network
        if allowed_location_accuracy_m is not None:
            point.allowed_location_accuracy_m = allowed_location_accuracy_m

        with self.atomic():
            point.save()
            self.audit.record(
                actor,
                action="qr.point.update",
                entity_type="office_qr_points",
                entity_id=point.id,
                before=before,
                after=snapshot(point, AUDITED_FIELDS),
            )
        return point

    def set_active(
        self, actor: Actor, point_id: uuid.UUID, *, active: bool
    ) -> OfficeQrPoint:
        """Включить или выключить точку.

        Выключенная точка отметок не принимает — это проверяет
        `attendance.services`, а не интерфейс. Удаления нет: на точку
        ссылаются отметки, и потерять ответ на вопрос «где приложили
        пропуск» нельзя.
        """
        self.access.require(actor, "qr_points.manage")
        point = self._require_point(actor, point_id)
        if point.is_active == active:
            return point

        before = snapshot(point, AUDITED_FIELDS)
        with self.atomic():
            point.is_active = active
            point.save(update_fields=["is_active", "updated_at"])
            self.audit.record(
                actor,
                action="qr.point.activate" if active else "qr.point.deactivate",
                entity_type="office_qr_points",
                entity_id=point.id,
                before=before,
                after=snapshot(point, AUDITED_FIELDS),
            )
        return point

    def reissue_static_token(
        self, actor: Actor, point_id: uuid.UUID
    ) -> IssuedPoint:
        """Новый секрет для статической точки.

        Прежний перестаёт работать в тот же миг: `token_version` растёт,
        и все коды прошлой версии становятся недействительны. Именно это
        нужно, когда наклейку сфотографировали.
        """
        self.access.require(actor, "qr_points.manage")
        point = self._require_point(actor, point_id)
        if point.qr_mode != "STATIC":
            raise Conflict(
                "Секрет есть только у статической точки; поворотная выдаёт "
                "код сама и хранить его не нужно",
                details={"qr_mode": point.qr_mode},
            )

        token = secrets.token_urlsafe(32)
        before = snapshot(point, AUDITED_FIELDS)
        with self.atomic():
            point.static_token_hash = _hash(token)
            point.token_version = (point.token_version or 1) + 1
            point.save(
                update_fields=["static_token_hash", "token_version", "updated_at"]
            )
            self.audit.record(
                actor,
                action="qr.point.token.reissue",
                entity_type="office_qr_points",
                entity_id=point.id,
                before=before,
                after=snapshot(point, AUDITED_FIELDS),
            )
        return IssuedPoint(point=point, static_token=token)

    # ------------------------------------------------------------ внутреннее

    def _require_point(self, actor: Actor, point_id: uuid.UUID) -> OfficeQrPoint:
        point = (
            OfficeQrPoint.objects.select_related("office", "office__region")
            .filter(id=point_id, organization_id=actor.organization_id)
            .first()
        )
        if point is None:
            # Чужая организация отвечает так же, как отсутствие записи:
            # иначе перебором идентификаторов пересчитываются чужие двери.
            raise NotFound("Точка отметки не найдена")
        self.access.require_office(actor, point.office_id)
        return point

    @staticmethod
    def _validate_mode(
        qr_mode: str, direction_mode: str, rotation_seconds: int | None
    ) -> None:
        if qr_mode not in QR_MODES:
            raise ValidationFailed(
                "Неизвестный режим точки",
                details={"qr_mode": qr_mode, "allowed": list(QR_MODES)},
            )
        if direction_mode not in QR_DIRECTION_MODES:
            raise ValidationFailed(
                "Неизвестное направление точки",
                details={"direction_mode": direction_mode,
                         "allowed": list(QR_DIRECTION_MODES)},
            )
        if qr_mode == "ROTATING":
            if rotation_seconds is None:
                raise ValidationFailed(
                    "Для поворотной точки нужен период смены кода",
                    details={"field": "rotation_seconds"},
                )
            _validate_rotation(rotation_seconds)


def _validate_rotation(seconds: int) -> None:
    if not MIN_ROTATION_SECONDS <= seconds <= MAX_ROTATION_SECONDS:
        raise ValidationFailed(
            f"Период смены кода должен быть от {MIN_ROTATION_SECONDS} "
            f"до {MAX_ROTATION_SECONDS} секунд",
            details={"rotation_seconds": seconds},
        )


__all__ = ["IssuedPoint", "QrPointService"]
