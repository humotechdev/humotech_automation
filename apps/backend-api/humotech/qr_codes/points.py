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

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.db.models import Count, Max
from django.utils import timezone

from humotech.core.enums import QR_DIRECTION_MODES, QR_MODES
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_code, clean_text
from humotech.qr_codes.models import OfficeQrPoint
from humotech.qr_codes import stickers
from humotech.qr_codes.stickers import token_hash

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
    "description",
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
    return token_hash(value)


def with_scans_today(points: list[OfficeQrPoint]) -> list[OfficeQrPoint]:
    """Сколько раз каждую точку сканировали сегодня — по дню её офиса.

    Считаются все попытки, и принятые, и отклонённые: HR смотрит сюда,
    чтобы понять, работает ли наклейка у двери, а сотня отказов «слишком
    далеко» отвечает на этот вопрос не хуже сотни отметок.

    «Сегодня» — в часовом поясе офиса точки, а не сервера: иначе в
    Ташкенте день начинался бы в пять утра. Запрос один на часовой пояс,
    а не на точку.
    """
    from humotech.attendance.models import AttendanceEvent

    if not points:
        return points
    now = timezone.now()
    by_zone: dict[str, list[OfficeQrPoint]] = {}
    for point in points:
        by_zone.setdefault(point.office.timezone, []).append(point)

    for zone, group in by_zone.items():
        local = now.astimezone(ZoneInfo(zone))
        start = datetime.combine(local.date(), time.min, tzinfo=ZoneInfo(zone))
        counts = dict(
            AttendanceEvent.objects.filter(
                qr_point_id__in=[point.id for point in group],
                occurred_at__gte=start,
            )
            .values_list("qr_point_id")
            .annotate(total=Count("id"))
            .values_list("qr_point_id", "total")
        )
        # Когда точку сканировали в последний раз — без ограничения по
        # дню. «Сегодня ноль» не отличает исправную наклейку в выходной
        # от сорванной неделю назад, а эта дата отличает.
        latest = dict(
            AttendanceEvent.objects.filter(
                qr_point_id__in=[point.id for point in group],
            )
            .values_list("qr_point_id")
            .annotate(last=Max("occurred_at"))
            .values_list("qr_point_id", "last")
        )
        for point in group:
            point.scans_today = counts.get(point.id, 0)
            point.last_scan_at = latest.get(point.id)
    return points


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
        ).select_related("office", "office__region", "created_by_user",
                         "created_by_user__employee")

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
        page = paginate(queryset, limit=limit, cursor=cursor)
        with_scans_today(list(page.items))
        return page

    def get(self, actor: Actor, point_id: uuid.UUID) -> OfficeQrPoint:
        self.access.require(actor, "qr_points.read")
        return with_scans_today([self._require_point(actor, point_id)])[0]

    # -------------------------------------------------------------- изменения

    def create(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID,
        name: str | None = None,
        code: str | None = None,
        description: str | None = None,
        direction_mode: str = "BOTH",
        qr_mode: str = "ROTATING",
        rotation_seconds: int | None = None,
        require_geolocation: bool = False,
        require_office_network: bool = False,
        allowed_location_accuracy_m: int | None = None,
    ) -> IssuedPoint:
        """Новая точка. Для `STATIC` сразу выпускается секрет.

        Секрет возвращается здесь и больше нигде и никогда.

        Ни код, ни название не обязательны. Код подбирается сам —
        уникальный внутри офиса. Название, если его не дали, берётся от
        типа точки: «Вход», «Выход» или «Вход и выход», с номером, если
        такая уже есть. Пустым оно не остаётся — строка без имени в
        списке точек нечитаема, а на печатном листе с кодом тем более.
        """
        self.access.require(actor, "qr_points.manage")
        office = self.access.require_office(actor, office_id)

        self._validate_mode(qr_mode, direction_mode, rotation_seconds)

        token = secrets.token_urlsafe(32) if qr_mode == "STATIC" else None
        with self.atomic():
            point = OfficeQrPoint.objects.create(
                organization_id=actor.organization_id,
                office=office,
                code=(
                    clean_code(code, field="code") if code
                    else _free_code(office.id)
                ),
                name=(
                    clean_text(name, field="name", max_length=255)
                    or _default_name(office.id, direction_mode)
                ),
                description=clean_text(
                    description, field="description", max_length=500
                ) if description else None,
                created_by_user_id=actor.user_id,
                direction_mode=direction_mode,
                qr_mode=qr_mode,
                rotation_seconds=(
                    rotation_seconds if qr_mode == "ROTATING" else None
                ),
                static_token_hash=_hash(token) if token else None,
                static_token=token,
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
        description: str | None = None,
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
        if description is not None:
            point.description = (
                clean_text(description, field="description", max_length=500)
                if description.strip() else None
            )
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
            point.static_token = token
            point.token_version = (point.token_version or 1) + 1
            point.rotated_at = timezone.now()
            point.save(
                update_fields=[
                    "static_token_hash", "static_token", "token_version",
                    "rotated_at", "updated_at",
                ]
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

    def sticker(self, actor: Actor, point_id: uuid.UUID) -> str | None:
        """Ссылка наклейки: посмотреть, скачать, распечатать заново.

        Требует права на управление точками — того же, что и выпуск.
        Право на чтение здесь не подходит: список точек могут смотреть и
        те, кому незачем знать сам код.

        Обращение записывается в журнал. Код — то же, что наклейка на
        стене, но кто его открывал и когда, знать полезно.

        `Conflict` — у точки нет сохранённого кода: она выпущена до
        того, как коды стали храниться. Такой код не восстановить, его
        заменяют новым.
        """
        self.access.require(actor, "qr_points.manage")
        point = self._require_point(actor, point_id)
        if point.qr_mode != "STATIC":
            raise Conflict(
                "Код наклейки есть только у статической точки; поворотная "
                "показывает его на экране сама",
                details={"qr_mode": point.qr_mode},
            )
        if not point.static_token:
            raise Conflict(
                "Код этой точки выпущен раньше, чем коды стали храниться, "
                "и восстановить его нельзя. Замените код — новый будет "
                "виден здесь всегда",
                details={"reason": "no_stored_token"},
            )
        self.audit.record(
            actor,
            action="qr.point.token.show",
            entity_type="office_qr_points",
            entity_id=point.id,
            before=None,
            after=None,
        )
        return stickers.link(point.static_token)

    def delete(self, actor: Actor, point_id: uuid.UUID) -> None:
        """Убрать точку совсем.

        Только ту, по которой никто не отмечался. Точка, попавшая хоть в
        одну отметку, перестаёт быть строкой справочника и становится
        частью истории: удалить её значит стереть ответ на вопрос «через
        какую дверь человек вошёл». Такую точку выключают — код
        перестаёт работать, а прошлые отметки остаются объяснимыми.
        """
        self.access.require(actor, "qr_points.manage")
        point = self._require_point(actor, point_id)

        from humotech.attendance.models import AttendanceEvent

        used = AttendanceEvent.objects.filter(qr_point_id=point.id).count()
        if used:
            raise Conflict(
                "По этой точке уже отмечались, поэтому удалить её нельзя — "
                "выключите её: код перестанет работать, а прошлые отметки "
                "останутся объяснимыми",
                details={"marks": used},
            )

        before = snapshot(point, AUDITED_FIELDS)
        with self.atomic():
            point.delete()
            self.audit.record(
                actor,
                action="qr.point.delete",
                entity_type="office_qr_points",
                entity_id=point_id,
                before=before,
                after=None,
            )

    # ------------------------------------------------------------ внутреннее

    def _require_point(self, actor: Actor, point_id: uuid.UUID) -> OfficeQrPoint:
        point = (
            OfficeQrPoint.objects.select_related(
                "office", "office__region", "created_by_user",
                "created_by_user__employee",
            )
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


#: Как зовётся точка, которую не назвали. Тип уже сказал, для чего она.
DIRECTION_NAMES = {
    "ENTRY": "Вход",
    "EXIT": "Выход",
    "BOTH": "Вход и выход",
}


def _default_name(office_id: uuid.UUID, direction_mode: str) -> str:
    """Название по типу точки, с номером при повторе.

    Номер добавляется только со второй такой точки: «Вход» и «Вход 2»
    читаются, а «Вход 1» у единственной двери выглядит ошибкой.
    """
    base = DIRECTION_NAMES.get(direction_mode, "Точка")
    taken = set(
        OfficeQrPoint.objects.filter(office_id=office_id).values_list(
            "name", flat=True
        )
    )
    if base not in taken:
        return base
    number = 2
    while f"{base} {number}" in taken:
        number += 1
    return f"{base} {number}"


def _free_code(office_id: uuid.UUID) -> str:
    """Свободный код точки в офисе: `QR-` и шесть знаков.

    Совпадение возможно, но маловероятно; на него — несколько попыток, а
    не бесконечный цикл. Уникальный ключ в базе всё равно второй рубеж.
    """
    taken = set(
        OfficeQrPoint.objects.filter(office_id=office_id).values_list(
            "code", flat=True
        )
    )
    for _ in range(8):
        candidate = f"QR-{secrets.token_hex(3).upper()}"
        if candidate not in taken:
            return candidate
    raise Conflict("Не удалось подобрать код точки — повторите попытку")


def _validate_rotation(seconds: int) -> None:
    if not MIN_ROTATION_SECONDS <= seconds <= MAX_ROTATION_SECONDS:
        raise ValidationFailed(
            f"Период смены кода должен быть от {MIN_ROTATION_SECONDS} "
            f"до {MAX_ROTATION_SECONDS} секунд",
            details={"rotation_seconds": seconds},
        )


__all__ = ["IssuedPoint", "QrPointService"]
