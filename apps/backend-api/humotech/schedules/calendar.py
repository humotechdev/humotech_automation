"""Праздники и переносы рабочих дней.

Исключение — это утверждение «в такой-то день работают не так, как
говорит график». Оно живёт одной строкой на дату: `uq_calendar_exceptions_
office_date` и `uq_calendar_exceptions_org_date` не дают завести на один
день два противоречащих утверждения. Эти два ограничения и есть запрет
неоднозначных пересечений, и держит его база, а не проверка в коде.

Отсюда следует форма API. Период задаётся `date_from`/`date_to` и
разворачивается в отдельную строку на каждый день в одной транзакции:
диапазон в колонках потребовал бы снять уникальность и переписать запрет
пересечений вручную на Python — то есть сделать хуже.

Область — организация или офис, третьего в схеме нет. Запрос на регион
принимается и разворачивается в строки по офисам региона на момент
создания. Своей строки у региона нет намеренно: колонка `region_id`
добавила бы третье измерение уникальности, а `(region, date)` и
`(office, date)` могут противоречить друг другу, и правило старшинства
между ними пришлось бы выдумать. HUMO его не задавала.

Приоритет между организацией и офисом уже определён и не здесь: исключение
офиса перекрывает общеорганизационное — см. `attendance/hr.py` и
`statistics.py`. Календарь читается на каждом расчёте заново, поэтому
изменение видно в присутствии, дашборде и аналитике сразу.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from django.db import IntegrityError
from django.db.models import Q

from humotech.core.enums import CALENDAR_EXCEPTION_TYPES
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate_on
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_text
from humotech.offices.models import Office
from humotech.schedules.models import CalendarException

CALENDAR_FIELDS = (
    "date", "name", "exception_type", "is_working_day", "reason",
    "is_active",
)

# Сколько дней можно завести одним запросом.
#
# Ограничение не техническое, а смысловое: перенос на год вперёд одним
# нажатием — это почти всегда опечатка в году, а не намерение. Разворот
# в строки при этом честный, и 366 строк в одной транзакции база держит
# без труда.
MAX_RANGE_DAYS = 366

#: Тип исключения сам говорит, рабочий это день или нет. Хранится всё
#: равно отдельным полем — так лежит в схеме, — но выводится отсюда,
#: чтобы «праздник, который рабочий день» нельзя было создать вовсе.
WORKING_BY_TYPE = {
    "HOLIDAY": False,
    "CLOSURE": False,
    "SHORT_DAY": True,
    "WORKING_WEEKEND": True,
}


class CalendarExceptionService(BaseService):
    """Чтение по `schedules.read`, изменение по `calendar.manage`.

    Отдельного разрешения на календарь заводить не потребовалось:
    `calendar.manage` уже есть в каталоге и означает ровно это —
    «праздники и переносы рабочих дней».
    """

    # ------------------------------------------------------------------ чтение

    def list(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        scope: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        exception_type: str | None = None,
        search: str | None = None,
        include_inactive: bool = False,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "schedules.read")

        queryset = CalendarException.objects.filter(
            organization_id=actor.organization_id
        ).select_related("office")

        if not include_inactive:
            # Умолчание — действующий календарь. Снятые исключения нужны,
            # когда разбираются в прошлом, а не когда смотрят, какие дни
            # в этом месяце рабочие.
            queryset = queryset.filter(is_active=True)

        if office_id:
            self.access.require_office(actor, office_id)
            # Исключения офиса — это его собственные ПЛЮС общие по
            # организации: в календаре офиса Навруз обязан быть виден,
            # даже если заведён на всю компанию.
            queryset = queryset.filter(
                Q(office_id=office_id) | Q(office__isnull=True)
            )
        elif region_id:
            self.access.require_region(actor, region_id)
            queryset = queryset.filter(
                Q(office__region_id=region_id) | Q(office__isnull=True)
            )
        elif scope == "organization":
            queryset = queryset.filter(office__isnull=True)
        else:
            visible = self.access.visible_office_ids(actor)
            if visible is not None:
                # Пустая область — это «ничего», а не «всё». Общие по
                # организации исключения при этом видны: они не про
                # конкретный офис и территорию не раскрывают.
                queryset = queryset.filter(
                    Q(office_id__in=visible) | Q(office__isnull=True)
                )

        if date_from:
            queryset = queryset.filter(date__gte=date_from)
        if date_to:
            queryset = queryset.filter(date__lte=date_to)
        if exception_type:
            queryset = queryset.filter(exception_type=exception_type)
        if search:
            queryset = queryset.filter(name__icontains=search.strip())

        # Листается по дате, а не по времени создания: календарь
        # читают по дням, и «следующая страница» обязана означать
        # «следующие дни», а не «заведённое раньше».
        return paginate_on(queryset, "date", limit=limit, cursor=cursor)

    def get(self, actor: Actor, exception_id: uuid.UUID) -> CalendarException:
        self.access.require(actor, "schedules.read")
        return self._require(actor, exception_id)

    # -------------------------------------------------------------- изменение

    def create(
        self,
        actor: Actor,
        *,
        name: str,
        exception_type: str,
        date_from: date,
        date_to: date | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> list[CalendarException]:
        """Создать исключение на день или на период.

        Возвращает список: период — это N строк, и клиент должен видеть
        именно их, а не одну запись с невидимым размахом.
        """
        self.access.require(actor, "calendar.manage")

        if exception_type not in CALENDAR_EXCEPTION_TYPES:
            raise ValidationFailed(
                "Неизвестный тип исключения",
                details={
                    "exception_type": exception_type,
                    "allowed": list(CALENDAR_EXCEPTION_TYPES),
                },
            )

        days = self._days(date_from, date_to)
        offices = self._targets(actor, office_id=office_id, region_id=region_id)
        name = clean_text(name, field="name", required=True, max_length=255)
        reason = clean_text(reason, field="reason")

        with self.atomic():
            try:
                # По строке за раз, а не `bulk_create`: ключ генерирует
                # база (`gen_random_uuid()`), и из пакетной вставки он не
                # возвращается — журналу нечего было бы записать в
                # `entity_id`. Операция редкая и ограничена 366 днями,
                # так что цена этой честности мала.
                created = [
                    CalendarException.objects.create(
                        organization_id=actor.organization_id,
                        office_id=office,
                        date=day,
                        name=name,
                        exception_type=exception_type,
                        is_working_day=WORKING_BY_TYPE[exception_type],
                        reason=reason,
                    )
                    for office in offices
                    for day in days
                ]
            except IntegrityError as exc:
                # Уникальность держит база. Перевод в понятную ошибку —
                # здесь, потому что только здесь известно, что именно
                # пытались завести.
                raise Conflict(
                    "На эти даты исключение уже заведено",
                    details={
                        "date_from": date_from.isoformat(),
                        "date_to": (date_to or date_from).isoformat(),
                        "office_ids": [str(o) for o in offices if o],
                    },
                ) from exc
            for row in created:
                self.audit.record(
                    actor,
                    action="calendar_exception.create",
                    entity_type="calendar_exceptions",
                    entity_id=row.id,
                    before=None,
                    after=snapshot(row, CALENDAR_FIELDS),
                )
        return created

    def update(
        self,
        actor: Actor,
        exception_id: uuid.UUID,
        *,
        name: str | None = None,
        exception_type: str | None = None,
        reason: str | None = None,
    ) -> CalendarException:
        """Меняются название, тип и основание.

        Дата и офис правкой поля не меняются: это не исправление опечатки,
        а перенос исключения на другой день или в другой офис, то есть
        снятие одного утверждения и создание другого. Пусть оно так и
        выглядит в журнале.
        """
        self.access.require(actor, "calendar.manage")
        row = self._require(actor, exception_id)
        before = snapshot(row, CALENDAR_FIELDS)

        if name is not None:
            row.name = clean_text(
                name, field="name", required=True, max_length=255
            )
        if reason is not None:
            row.reason = clean_text(reason, field="reason")
        if exception_type is not None:
            if exception_type not in CALENDAR_EXCEPTION_TYPES:
                raise ValidationFailed(
                    "Неизвестный тип исключения",
                    details={
                        "exception_type": exception_type,
                        "allowed": list(CALENDAR_EXCEPTION_TYPES),
                    },
                )
            row.exception_type = exception_type
            # Признак рабочего дня следует за типом, а не задаётся
            # отдельно: «праздник, который рабочий день» — не состояние,
            # которое кому-то нужно, а рассогласование.
            row.is_working_day = WORKING_BY_TYPE[exception_type]

        with self.atomic():
            row.save()
            self.audit.record(
                actor,
                action="calendar_exception.update",
                entity_type="calendar_exceptions",
                entity_id=row.id,
                before=before,
                after=snapshot(row, CALENDAR_FIELDS),
            )
        return row

    def deactivate(
        self, actor: Actor, exception_id: uuid.UUID
    ) -> CalendarException:
        """Снять исключение с действия, оставив строку.

        Так снимают отменённый приказом перенос: сам факт «в марте
        собирались работать в субботу, потом отменили» через полгода
        объясняет расхождение в табеле, а удалённая строка не объясняет
        ничего.

        На расчёт снятое исключение не влияет: все четыре читателя
        календаря — присутствие, аналитика, статистика и отсутствия —
        спрашивают только действующие.
        """
        return self._set_active(
            actor, exception_id, active=False,
            action="calendar_exception.deactivate",
            already="Исключение уже снято",
        )

    def reactivate(
        self, actor: Actor, exception_id: uuid.UUID
    ) -> CalendarException:
        """Вернуть снятое исключение в действие.

        Дата к этому моменту могла быть занята: пока исключение было
        снято, на тот же день завели другое. Это проверяется здесь и
        отвечает понятным отказом — без проверки ответ пришёл бы из
        ограничения целостности, то есть сообщением про индекс вместо
        сообщения про календарь.
        """
        row = self._require(actor, exception_id)
        if not row.is_active:
            taken = CalendarException.objects.filter(
                organization_id=actor.organization_id,
                date=row.date,
                office_id=row.office_id,
                is_active=True,
            ).exclude(id=row.id)
            if taken.exists():
                raise Conflict(
                    "На эту дату уже действует другое исключение: "
                    "снимите его или оставьте это снятым",
                    details={"date": row.date.isoformat(),
                             "office_id": str(row.office_id)
                             if row.office_id else None},
                )
        return self._set_active(
            actor, exception_id, active=True,
            action="calendar_exception.reactivate",
            already="Исключение уже действует",
        )

    def _set_active(
        self,
        actor: Actor,
        exception_id: uuid.UUID,
        *,
        active: bool,
        action: str,
        already: str,
    ) -> CalendarException:
        self.access.require(actor, "calendar.manage")
        row = self._require(actor, exception_id)
        if row.is_active == active:
            raise Conflict(already, details={"is_active": row.is_active})

        before = snapshot(row, CALENDAR_FIELDS)
        with self.atomic():
            row.is_active = active
            row.save(update_fields=["is_active", "updated_at"])
            self.audit.record(
                actor,
                action=action,
                entity_type="calendar_exceptions",
                entity_id=row.id,
                before=before,
                after=snapshot(row, CALENDAR_FIELDS),
            )
        return row

    def delete(self, actor: Actor, exception_id: uuid.UUID) -> None:
        """Удалить строку целиком: её не должно было быть вовсе.

        Отличается от снятия намеренно. Снятие говорит «так было, потом
        отменили» и остаётся в календаре прошлого; удаление говорит
        «этого не было» и применяется к опечаткам — заведённому не на
        тот день или не в тот офис.

        Сама операция остаётся в журнале вместе со снимком удалённого,
        так что восстановить, что именно снесли, можно.
        """
        self.access.require(actor, "calendar.manage")
        row = self._require(actor, exception_id)
        before = snapshot(row, CALENDAR_FIELDS)
        row_id = row.id

        with self.atomic():
            row.delete()
            self.audit.record(
                actor,
                action="calendar_exception.delete",
                entity_type="calendar_exceptions",
                entity_id=row_id,
                before=before,
                after=None,
            )

    # ----------------------------------------------------------------- частное

    def _require(self, actor: Actor, exception_id: uuid.UUID) -> CalendarException:
        row = (
            CalendarException.objects.select_related("office")
            .filter(id=exception_id, organization_id=actor.organization_id)
            .first()
        )
        if row is None:
            # Чужая организация отвечает как отсутствие записи.
            raise NotFound("Исключение календаря не найдено")
        if row.office_id is not None:
            self.access.require_office(actor, row.office_id)
        elif self.access.visible_office_ids(actor) is not None:
            # Общеорганизационное исключение правит только тот, чья область
            # — вся организация. Администратор одного офиса такой день в
            # календаре видит, но отменить Навруз всей компании не может.
            raise NotFound("Исключение календаря не найдено")
        return row

    @staticmethod
    def _days(date_from: date, date_to: date | None) -> list[date]:
        last = date_to or date_from
        if last < date_from:
            raise ValidationFailed(
                "Конец периода раньше начала",
                details={
                    "date_from": date_from.isoformat(),
                    "date_to": last.isoformat(),
                },
            )
        span = (last - date_from).days + 1
        if span > MAX_RANGE_DAYS:
            raise ValidationFailed(
                f"За один раз можно завести не больше {MAX_RANGE_DAYS} дней",
                details={"days": span, "limit": MAX_RANGE_DAYS},
            )
        return [date_from + timedelta(days=offset) for offset in range(span)]

    def _targets(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID | None,
        region_id: uuid.UUID | None,
    ) -> list[uuid.UUID | None]:
        """Во что разворачивается запрошенная область.

        `None` в списке означает строку без офиса, то есть исключение на
        всю организацию.
        """
        if office_id and region_id:
            raise ValidationFailed(
                "Укажите либо офис, либо регион, но не оба",
                details={
                    "office_id": str(office_id),
                    "region_id": str(region_id),
                },
            )
        if office_id:
            self.access.require_office(actor, office_id)
            return [office_id]
        if region_id:
            self.access.require_region(actor, region_id)
            offices = list(
                Office.objects.filter(
                    organization_id=actor.organization_id,
                    region_id=region_id,
                    status="ACTIVE",
                ).values_list("id", flat=True)
            )
            if not offices:
                raise ValidationFailed(
                    "В регионе нет действующих офисов — "
                    "исключение не к чему привязать",
                    details={"region_id": str(region_id)},
                )
            return list(offices)

        # Область не указана — исключение на всю организацию. Заводит его
        # только тот, кто видит организацию целиком: иначе администратор
        # одного офиса объявил бы выходной всей компании.
        if self.access.visible_office_ids(actor) is not None:
            raise ValidationFailed(
                "Исключение на всю организацию заводит только тот, чья "
                "область — вся организация. Укажите офис или регион.",
                details={"office_id": None, "region_id": None},
            )
        return [None]


__all__ = ["CalendarExceptionService", "MAX_RANGE_DAYS", "WORKING_BY_TYPE"]
