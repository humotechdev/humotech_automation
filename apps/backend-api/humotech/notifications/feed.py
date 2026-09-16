"""Лента событий кадровика: что произошло и чем надо заняться.

Это НЕ очередь отправки из `service.py`. Та таблица отвечает на вопрос
«ушло ли сообщение сотруднику», а здесь собрано то, что случилось в
кадровом контуре и ждёт человека: новая заявка, загруженная справка,
незакрытый выход, обращение из Telegram.

Своей таблицы у ленты нет намеренно. Каждое событие уже записано там,
где произошло, — в заявке, в сессии, в обращении. Вторая копия означала
бы два источника правды и вечное расхождение между ними: заявку
одобрили, а в ленте она всё ещё «ждёт решения». Поэтому лента —
слияние восьми выборок по одному ключу `created_at`, ровно как общая
очередь заявок в `core/queue_views.py`.

Хранится единственное, чего из данных не вывести: кто из кадровиков это
событие уже видел (`FeedRead`). Прочтение у каждого своё — очередь
разбирают вдвоём, и отметка одного не должна гасить событие у другого.

Идентификатор события составной, `вид:запись`. Он выводится из данных,
поэтому переживает пересборку ленты и не требует ни таблицы, ни
последовательности. По нему же всегда можно вернуться к исходной
строке — и проверить доступ к ней заново.

Права проверяются ПОИСТОЧНИКОВО. У каждого источника своё разрешение и
свой путь до офиса сотрудника; источник, на который прав нет, не даёт ни
строки — и не даёт отказа: лента общая, и отсутствие права на обращения
не должно закрывать заявки. Область видимости при этом не смягчается
нигде: кадровик одного офиса не увидит в ленте чужой офис.

Чего здесь нет и почему:

  * «Истекает срок документа» — у `employee_documents` нет даты
    окончания. Выдумывать её нельзя, а показывать событие без даты
    незачем: срок и есть всё его содержание;
  * тела документов, номеров паспорта, токенов и ответов провайдера.
    В ленту попадают только безопасные сведения о событии.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from humotech.absences.models import AbsenceDocument, AbsenceRequest
from humotech.absences.services import AbsenceService, hr_period_summary
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceSession
from humotech.core.errors import NotFound, ValidationFailed
from humotech.core.pagination import Cursor, normalize_limit
from humotech.core.rbac import Actor
from humotech.core.service import BaseService
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.services import current_primary_assignment_filter
from humotech.notifications.models import FeedRead, Notification
from humotech.questions.models import EmployeeQuestion
from humotech.reports.models import ExportJob

#: Глубина ленты в днях. Лента — это «что сейчас на руках», а не архив:
#: заявку месячной давности ищут в «Заявках», а не в колокольчике.
FEED_DAYS = 30

#: Потолок строк на ОДИН источник. Нужен не ради скорости, а ради
#: честного ответа: слияние идёт в памяти, и без потолка один шумный
#: источник (например, сотня незакрытых сессий) вытеснил бы из ленты
#: всё остальное.
SOURCE_LIMIT = 100

#: Виды событий. Значение `type` в ответе и первая половина `id`.
TYPE_ABSENCE = "absence_request"
TYPE_SICK = "sick_leave"
TYPE_CANCEL = "absence_cancel"
TYPE_DOCUMENT = "absence_document"
TYPE_CORRECTION = "attendance_correction"
TYPE_OPEN_SESSION = "attendance_open"
TYPE_QUESTION = "question"
TYPE_DELIVERY = "delivery_error"
TYPE_REPORT = "report_ready"
TYPE_EMPLOYEE = "employee_added"

#: К какой вкладке фильтра относится вид события. Вкладки в сумме дают
#: весь набор — иначе часть событий не видна ни на одной, кроме «Все».
GROUPS: dict[str, str] = {
    TYPE_ABSENCE: "requests",
    TYPE_SICK: "requests",
    TYPE_CANCEL: "requests",
    TYPE_DOCUMENT: "documents",
    TYPE_CORRECTION: "attendance",
    TYPE_OPEN_SESSION: "attendance",
    TYPE_QUESTION: "questions",
    TYPE_DELIVERY: "system",
    TYPE_REPORT: "system",
    TYPE_EMPLOYEE: "system",
}

#: Виды событий одним набором — для схемы и проверок. Порядок тот же,
#: в котором они объявлены выше.
FEED_TYPES = tuple(GROUPS)

#: Насколько событие срочное. Красный указатель в ленте — только у
#: CRITICAL: если им помечать всё подряд, он перестаёт что-либо значить.
FEED_PRIORITIES = ("NORMAL", "HIGH", "CRITICAL")

#: Вкладки фильтра в порядке показа.
FILTERS = (
    ("all", "Все"),
    ("unread", "Непрочитанные"),
    ("action", "Требуют действия"),
    ("requests", "Заявки"),
    ("documents", "Документы"),
    ("attendance", "Посещаемость"),
    ("questions", "Обращения"),
    ("system", "Системные"),
)

#: Заявка, по которой решение ещё не принято.
OPEN_REQUEST_STATUSES = ("SUBMITTED", "IN_REVIEW")
#: Вид заявки «на само отсутствие», без отмены.
OWN_KINDS = ("CREATE", "EXTEND")
#: Код типа отсутствия, у которого своя карточка (§6 задания).
SICK_CODE = "SICK_LEAVE"

#: Сколько часов открытая сессия остаётся обычным делом. Дольше —
#: человек ушёл, не отметившись, и это уже разбирательство.
OPEN_SESSION_CRITICAL_HOURS = 12

#: Подписи состояний. Отдельно по видам: «Одобрена» у заявки и
#: «Подтверждён» у справки — разные слова о разных вещах.
ABSENCE_STATUS_LABELS = {
    "DRAFT": "Черновик",
    "SUBMITTED": "На рассмотрении",
    "IN_REVIEW": "На рассмотрении",
    "APPROVED": "Одобрена",
    "REJECTED": "Отклонена",
    "CANCELLED": "Отменена",
}
CANCEL_STATUS_LABELS = {
    **ABSENCE_STATUS_LABELS,
    "SUBMITTED": "Запрошена отмена",
    "IN_REVIEW": "Запрошена отмена",
}
DOCUMENT_STATUS_LABELS = {
    "PENDING": "На проверке",
    "VERIFIED": "Подтверждён",
    "REJECTED": "Отклонён",
}
CORRECTION_STATUS_LABELS = {
    "DRAFT": "Черновик",
    "SUBMITTED": "На рассмотрении",
    "IN_REVIEW": "На рассмотрении",
    "APPROVED": "Одобрено",
    "REJECTED": "Отклонено",
    "CANCELLED": "Отменено",
}
QUESTION_STATUS_LABELS = {
    "NEW": "Ждёт ответа",
    "IN_PROGRESS": "В работе",
    "WAITING_EMPLOYEE": "Ждём сотрудника",
    "CLOSED": "Закрыто",
}

#: Длина вторичной строки. Короткая по существу: в ленте нельзя
#: показывать ни описание целиком, ни текст документа.
SNIPPET = 90


@dataclass(frozen=True)
class Event:
    """Одно событие ленты. Собрано из строки-источника, не хранится."""

    type: str
    entity_id: uuid.UUID
    created_at: datetime
    title: str
    short_text: str
    status: str
    status_label: str
    priority: str
    requires_action: bool
    related_entity_type: str
    related_entity_id: uuid.UUID
    action_url: str
    action_title: str
    employee_id: uuid.UUID | None = None
    employee_name: str = ""
    office_id: uuid.UUID | None = None
    office_name: str | None = None
    read_at: datetime | None = None

    @property
    def id(self) -> str:
        return f"{self.type}:{self.entity_id}"

    @property
    def group(self) -> str:
        return GROUPS[self.type]

    @property
    def order(self) -> tuple:
        # Тот же ключ, которым сортируют страницы остальные списки.
        return (self.created_at, str(self.entity_id))


def event_json(event: Event) -> dict:
    """Строка ленты. Состав полей задан заданием и не сокращается."""
    return {
        "id": event.id,
        "type": event.type,
        "group": event.group,
        "title": event.title,
        "short_text": event.short_text,
        "employee_id": str(event.employee_id) if event.employee_id else None,
        "employee_name": event.employee_name,
        "office_id": str(event.office_id) if event.office_id else None,
        "office_name": event.office_name,
        "status": event.status,
        "status_label": event.status_label,
        "priority": event.priority,
        "requires_action": event.requires_action,
        "created_at": event.created_at,
        "read_at": event.read_at,
        "related_entity_type": event.related_entity_type,
        "related_entity_id": str(event.related_entity_id),
        "action_url": event.action_url,
        "action_title": event.action_title,
    }


class FeedService(BaseService):
    """Сборка ленты, её счётчиков и отметок прочтения.

    Ни одного собственного разрешения у сервиса нет: право спрашивает
    каждый источник за себя. Пользователь без единого кадрового права
    получит пустую ленту, а не отказ, — колокольчик есть у всех.
    """

    # ------------------------------------------------------------------ чтение

    def page(
        self,
        actor: Actor,
        *,
        scope: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        """Страница ленты вместе со счётчиками вкладок.

        Счётчики приходят тем же ответом, а не отдельным запросом: число
        у колокольчика и список под ним обязаны описывать одно и то же
        состояние. Два запроса дали бы два разных момента и вечное
        «в списке пять, в кружке шесть».
        """
        wanted = self._known_scope(scope)
        size = normalize_limit(limit)
        events = self._events(actor)

        counts = self._counts(events)
        chosen = [one for one in events if self._matches(one, wanted)]
        position = Cursor.decode(cursor) if cursor else None
        if position is not None:
            chosen = [
                one for one in chosen
                if one.order < (position.created_at, str(position.id))
            ]

        page = chosen[:size]
        has_more = len(chosen) > size
        return {
            "items": [event_json(one) for one in page],
            "counts": counts,
            "next_cursor": (
                Cursor(created_at=page[-1].created_at, id=page[-1].entity_id).encode()
                if page and has_more
                else None
            ),
            "has_more": has_more,
            "window_days": FEED_DAYS,
        }

    def counts(self, actor: Actor) -> dict:
        """Только счётчики: для колокольчика на страницах без ленты."""
        return self._counts(self._events(actor))

    def detail(self, actor: Actor, event_id: str) -> dict:
        """Карточка события: общие поля плюс блок своего вида.

        Доступ проверяется ЗАНОВО, от исходной строки: идентификатор
        события выводится из данных, то есть угадываем, и открывать по
        нему чужую заявку нельзя. Список тут не при чём — его могли и
        не запрашивать.
        """
        kind, entity_id = self._parse(event_id)
        builder = {
            TYPE_ABSENCE: self._absence_detail,
            TYPE_SICK: self._absence_detail,
            TYPE_CANCEL: self._absence_detail,
            TYPE_DOCUMENT: self._document_detail,
            TYPE_CORRECTION: self._correction_detail,
            TYPE_OPEN_SESSION: self._session_detail,
            TYPE_QUESTION: self._question_detail,
            TYPE_DELIVERY: self._delivery_detail,
            TYPE_REPORT: self._report_detail,
            TYPE_EMPLOYEE: self._employee_detail,
        }[kind]
        event, extra = builder(actor, entity_id)
        if event.type != kind:
            # Вид в ключе не совпал с видом найденной записи: заявка на
            # отпуск, запрошенная как больничный. Ключ угадан неверно —
            # и отвечать надо как на несуществующее событие, а не
            # подсовывать другое.
            raise NotFound("Событие не найдено")
        event = replace(event, read_at=self._read_at(actor, event))
        place = self._places({event.employee_id} if event.employee_id else set())
        card = event_json(event)
        card.update(
            {
                "employee": (
                    {
                        "id": str(event.employee_id),
                        "full_name": event.employee_name,
                        **(place.get(event.employee_id) or {}),
                    }
                    if event.employee_id
                    else None
                ),
                **extra,
            }
        )
        return card

    # ---------------------------------------------------------------- прочтение

    def mark_read(self, actor: Actor, event_id: str) -> dict:
        """Отметить одно событие прочитанным ЭТИМ пользователем.

        Доступ проверяется так же, как при открытии карточки: отметка —
        это запись в базу, и делать её по чужому событию нельзя, даже
        если видимого вреда от неё нет.
        """
        kind, entity_id = self._parse(event_id)
        self.detail(actor, event_id)
        with self.atomic():
            FeedRead.objects.get_or_create(
                user_id=actor.user_id,
                event_type=kind,
                entity_id=entity_id,
                defaults={"organization_id": actor.organization_id},
            )
        return self.counts(actor)

    def mark_all(self, actor: Actor) -> dict:
        """Прочитать всё, что видно ЭТОМУ пользователю.

        Именно видно: отметки пишутся по тем событиям, которые вернула
        его же лента. Общего `UPDATE` здесь быть не может — он пометил
        бы и чужие офисы, и виды, на которые прав нет.
        """
        events = self._events(actor)
        fresh = [one for one in events if one.read_at is None]
        moment = timezone.now()
        if fresh:
            with self.atomic():
                FeedRead.objects.bulk_create(
                    [
                        FeedRead(
                            user_id=actor.user_id,
                            organization_id=actor.organization_id,
                            event_type=one.type,
                            entity_id=one.entity_id,
                        )
                        for one in fresh
                    ],
                    # Второй кадровик мог отметить то же событие
                    # мгновением раньше — это не ошибка.
                    ignore_conflicts=True,
                )
        # Счётчики — уже ПОСЛЕ отметок. Считать их по набору, собранному
        # до записи, значило бы вернуть прежнее «непрочитано: 6» ровно
        # в ответ на «прочитать все».
        marked = {(one.type, one.entity_id) for one in fresh}
        events = [
            replace(one, read_at=moment) if (one.type, one.entity_id) in marked
            else one
            for one in events
        ]
        return {**self._counts(events), "marked": len(fresh)}

    # ------------------------------------------------------------------ сборка

    def _events(self, actor: Actor) -> list[Event]:
        """Все события окна, слитые по времени и с отметками прочтения."""
        since = timezone.now() - timedelta(days=FEED_DAYS)
        rows: list[Event] = []
        for source in (
            self._absences,
            self._documents,
            self._corrections,
            self._open_sessions,
            self._questions,
            self._delivery_errors,
            self._reports,
            self._new_employees,
        ):
            rows += source(actor, since)

        rows.sort(key=lambda one: one.order, reverse=True)
        return self._with_reads(actor, self._with_places(rows))

    def _with_places(self, rows: list[Event]) -> list[Event]:
        """Офис проставляется одним запросом на всю ленту, а не на строку."""
        places = self._places({one.employee_id for one in rows if one.employee_id})
        result = []
        for one in rows:
            place = places.get(one.employee_id) if one.employee_id else None
            if place and one.office_id is None:
                result.append(
                    replace(
                        one,
                        office_id=place["office_id"],
                        office_name=place["office_name"],
                    )
                )
            else:
                result.append(one)
        return result

    def _with_reads(self, actor: Actor, rows: list[Event]) -> list[Event]:
        marks = {
            (row_type, entity_id): read_at
            for row_type, entity_id, read_at in FeedRead.objects.filter(
                user_id=actor.user_id,
                entity_id__in=[one.entity_id for one in rows],
            ).values_list("event_type", "entity_id", "read_at")
        }
        return [
            replace(one, read_at=marks.get((one.type, one.entity_id))) for one in rows
        ]

    def _read_at(self, actor: Actor, event: Event) -> datetime | None:
        return (
            FeedRead.objects.filter(
                user_id=actor.user_id,
                event_type=event.type,
                entity_id=event.entity_id,
            )
            .values_list("read_at", flat=True)
            .first()
        )

    def _counts(self, events: list[Event]) -> dict:
        counts = {key: 0 for key, _ in FILTERS}
        for one in events:
            counts["all"] += 1
            counts[one.group] += 1
            if one.read_at is None:
                counts["unread"] += 1
            if one.requires_action:
                counts["action"] += 1
        return counts

    @staticmethod
    def _matches(event: Event, scope: str) -> bool:
        if scope == "all":
            return True
        if scope == "unread":
            return event.read_at is None
        if scope == "action":
            return event.requires_action
        return event.group == scope

    # ----------------------------------------------------------- источники

    def _absences(self, actor: Actor, since: datetime) -> list[Event]:
        """Заявки на отсутствие: отпуск, больничный, отмена."""
        rows = self._absence_queue(actor)
        if rows is None:
            return []
        return [
            self._absence_event(row)
            for row in rows.filter(created_at__gte=since).order_by(
                "-created_at", "-id"
            )[:SOURCE_LIMIT]
        ]

    def _absence_event(self, row: AbsenceRequest) -> Event:
        sick = row.absence_type.code == SICK_CODE
        cancel = row.request_kind == "CANCEL"
        if cancel:
            kind = TYPE_CANCEL
            what = "больничного" if sick else "отпуска"
            title = f"Запрошена отмена {what}"
            labels = CANCEL_STATUS_LABELS
        elif sick:
            kind = TYPE_SICK
            title = "Продление больничного" if row.request_kind == "EXTEND" else (
                "Новый больничный"
            )
            labels = ABSENCE_STATUS_LABELS
        else:
            kind = TYPE_ABSENCE
            title = "Продление заявки на отпуск" if row.request_kind == "EXTEND" else (
                "Новая заявка на отпуск"
            )
            labels = ABSENCE_STATUS_LABELS
        return Event(
            type=kind,
            entity_id=row.id,
            created_at=row.created_at,
            title=title,
            short_text=_period_text(row),
            status=row.status,
            status_label=labels.get(row.status, row.status),
            priority=("HIGH" if row.status in OPEN_REQUEST_STATUSES else "NORMAL"),
            requires_action=row.status in OPEN_REQUEST_STATUSES,
            related_entity_type="absence_requests",
            related_entity_id=row.id,
            action_url=f"/requests?request={row.id}",
            action_title="Открыть больничный" if sick else "Открыть заявку",
            employee_id=row.employee_id,
            employee_name=_full_name(row.employee),
        )

    def _documents(self, actor: Actor, since: datetime) -> list[Event]:
        """Справки и больничные листы, приложенные к заявкам.

        Область берётся из самой очереди заявок: документ виден тому,
        кому видна заявка. Отдельного правила у него нет и быть не
        должно — иначе справка пережила бы скрытую заявку.
        """
        rows = self._absence_queue(actor)
        if rows is None:
            return []
        documents = (
            AbsenceDocument.objects.filter(
                organization_id=actor.organization_id,
                absence_request_id__in=rows.values("id"),
                created_at__gte=since,
            )
            .select_related(
                "file", "absence_request__employee", "absence_request__absence_type"
            )
            .order_by("-created_at", "-id")[:SOURCE_LIMIT]
        )
        return [self._document_event(row) for row in documents]

    def _corrections(self, actor: Actor, since: datetime) -> list[Event]:
        """Заявки на исправление отметки."""
        if not self.access.has(actor, "attendance.read"):
            return []
        rows = self._sub(AttendanceHrService).correction_queue(actor)
        return [
            self._correction_event(row)
            for row in rows.filter(created_at__gte=since).order_by(
                "-created_at", "-id"
            )[:SOURCE_LIMIT]
        ]

    def _correction_event(self, row) -> Event:
        return Event(
            type=TYPE_CORRECTION,
            entity_id=row.id,
            created_at=row.created_at,
            title="Исправление отметки",
            short_text=_correction_text(row),
            status=row.status,
            status_label=CORRECTION_STATUS_LABELS.get(row.status, row.status),
            priority="HIGH" if row.status in OPEN_REQUEST_STATUSES else "NORMAL",
            requires_action=row.status in OPEN_REQUEST_STATUSES,
            related_entity_type="attendance_correction_requests",
            related_entity_id=row.id,
            action_url=f"/requests?tab=fixes&request={row.id}",
            action_title="Открыть исправление",
            employee_id=row.employee_id,
            employee_name=_full_name(row.employee),
        )

    def _open_sessions(self, actor: Actor, since: datetime) -> list[Event]:
        """Незакрытые рабочие сессии: человек вошёл и не отметил выход.

        Событие живёт ровно столько, сколько сессия остаётся открытой:
        закрыли выход — событие исчезло само. Отдельного «решено» ему
        не нужно, и второй строки о том же дне не появится.
        """
        if not self.access.has(actor, "attendance.read"):
            return []
        # Окно отсекается здесь, а не параметрами `sessions`: там период
        # задают ПАРОЙ дат, и «с такого-то дня» без второй границы
        # означает ровно один день, а не «с тех пор».
        page = self._sub(AttendanceHrService).sessions(
            actor, only_open=True, limit=SOURCE_LIMIT
        )
        now = timezone.now()
        return [
            self._session_event(row, now)
            for row in page.items
            if row.created_at >= since
        ]

    def _session_event(self, row, now: datetime) -> Event:
        hours = (now - row.started_at).total_seconds() / 3600
        office = row.office.name if row.office_id else None
        return Event(
            type=TYPE_OPEN_SESSION,
            entity_id=row.id,
            created_at=row.created_at,
            title="Не закрыт выход",
            short_text=f"Вход {_clock(row.started_at)}, {office or 'офис не указан'}",
            status=row.status,
            status_label="Сессия открыта",
            priority="CRITICAL" if hours >= OPEN_SESSION_CRITICAL_HOURS else "HIGH",
            requires_action=True,
            related_entity_type="attendance_sessions",
            related_entity_id=row.id,
            action_url=(
                f"/attendance?date={row.started_at.date().isoformat()}"
                f"&flag=open&employee={row.employee_id}"
            ),
            action_title="Открыть посещаемость",
            employee_id=row.employee_id,
            employee_name=_full_name(row.employee),
            office_id=row.office_id,
            office_name=office,
        )

    def _questions(self, actor: Actor, since: datetime) -> list[Event]:
        """Обращения сотрудников из Telegram."""
        if not self.access.has(actor, "questions.read"):
            return []
        rows = self._scoped(
            actor, EmployeeQuestion.objects.select_related("employee")
        ).filter(created_at__gte=since)
        return [
            self._question_event(row)
            for row in rows.order_by("-created_at", "-id")[:SOURCE_LIMIT]
        ]

    def _delivery_errors(self, actor: Actor, since: datetime) -> list[Event]:
        """Сообщения, которые не дошли до сотрудника.

        Время события — время последней неудачи (`updated_at`), а не
        создания строки: уведомление могли завести три недели назад,
        а упасть оно могло сегодня.
        """
        if not self.access.has(actor, "notifications.read"):
            return []
        rows = self._scoped(
            actor,
            Notification.objects.select_related("employee").filter(status="FAILED"),
        ).filter(updated_at__gte=since)
        return [
            self._delivery_event(row)
            for row in rows.order_by("-updated_at", "-id")[:SOURCE_LIMIT]
        ]

    def _delivery_event(self, row: Notification) -> Event:
        return Event(
            type=TYPE_DELIVERY,
            entity_id=row.id,
            created_at=row.updated_at,
            title="Ошибка доставки сообщения",
            short_text=_snippet(row.title or row.notification_type, SNIPPET),
            status=row.status,
            status_label="Не доставлено",
            priority="CRITICAL",
            requires_action=True,
            related_entity_type="notifications",
            related_entity_id=row.id,
            action_url=f"/notifications?view=delivery&id={row.id}",
            action_title="Открыть историю отправки",
            employee_id=row.employee_id,
            employee_name=_full_name(row.employee),
        )

    def _reports(self, actor: Actor, since: datetime) -> list[Event]:
        """Готовые выгрузки — только СВОИ.

        У выгрузки нет ни сотрудника, ни офиса: область файла задана его
        фильтрами, а не местом работы. Поэтому территориальное правило
        сюда не применяется, а применяется единственное осмысленное:
        о готовности файла сообщают тому, кто его заказал.
        """
        rows = ExportJob.objects.filter(
            organization_id=actor.organization_id,
            requested_by_user_id=actor.user_id,
            status="SUCCEEDED",
            hidden_at__isnull=True,
            finished_at__gte=since,
        ).order_by("-finished_at", "-id")[:SOURCE_LIMIT]
        return [self._report_event(row) for row in rows]

    def _report_event(self, row: ExportJob) -> Event:
        return Event(
            type=TYPE_REPORT,
            entity_id=row.id,
            created_at=row.finished_at or row.created_at,
            title="Отчёт готов",
            short_text=f"{row.kind} · {row.fmt.upper()}",
            status=row.status,
            status_label="Готов",
            priority="NORMAL",
            requires_action=False,
            related_entity_type="export_jobs",
            related_entity_id=row.id,
            action_url="/reports",
            action_title="Открыть отчёты",
        )

    def _new_employees(self, actor: Actor, since: datetime) -> list[Event]:
        """Заведённые карточки сотрудников."""
        if not self.access.has(actor, "employees.read"):
            return []
        rows = self._scoped(actor, Employee.objects.all(), own=True).filter(
            created_at__gte=since
        )
        return [
            self._employee_event(row)
            for row in rows.order_by("-created_at", "-id")[:SOURCE_LIMIT]
        ]

    def _employee_event(self, row: Employee) -> Event:
        return Event(
            type=TYPE_EMPLOYEE,
            entity_id=row.id,
            created_at=row.created_at,
            title="Новый сотрудник добавлен",
            short_text=f"Табельный номер {row.employee_number}",
            status=row.employment_status,
            status_label=(
                "Работает" if row.employment_status == "ACTIVE"
                else row.employment_status
            ),
            priority="NORMAL",
            requires_action=False,
            related_entity_type="employees",
            related_entity_id=row.id,
            action_url=f"/employees/{row.id}",
            action_title="Открыть карточку сотрудника",
            employee_id=row.id,
            employee_name=_full_name(row),
        )

    # -------------------------------------------------------------- карточки

    def _absence_detail(self, actor: Actor, entity_id: uuid.UUID):
        rows = self._absence_queue(actor)
        row = rows.filter(id=entity_id).first() if rows is not None else None
        if row is None:
            raise NotFound("Событие не найдено")
        event = self._absence_event(row)
        documents = sorted(
            row.documents.all(), key=lambda one: one.created_at, reverse=True
        )
        latest = documents[0] if documents else None
        return event, {
            "comment": row.employee_comment,
            "author": _person_by_id(row.reviewed_by_user_id),
            "occurred_at": row.submitted_at or row.created_at,
            "absence": {
                "type_name": row.absence_type.name,
                "type_code": row.absence_type.code,
                "request_kind": row.request_kind,
                "is_extension": row.request_kind == "EXTEND",
                "first_day": _day(row.requested_start_at),
                "last_day": _day(row.requested_end_at),
                "requires_document": row.absence_type.requires_document,
                "document": _document_json(latest),
                "review_comment": row.review_comment,
                **hr_period_summary(row),
            },
        }

    def _document_detail(self, actor: Actor, entity_id: uuid.UUID):
        rows = self._absence_queue(actor)
        row = (
            AbsenceDocument.objects.filter(
                id=entity_id,
                organization_id=actor.organization_id,
                absence_request_id__in=rows.values("id"),
            )
            .select_related(
                "file", "absence_request__employee", "absence_request__absence_type"
            )
            .first()
            if rows is not None
            else None
        )
        if row is None:
            raise NotFound("Событие не найдено")
        request = row.absence_request
        event = self._document_event(row)
        return event, {
            "comment": row.verification_comment or request.employee_comment,
            "author": _person_by_id(row.verified_by_user_id),
            "occurred_at": row.created_at,
            "absence": {
                "type_name": request.absence_type.name,
                "type_code": request.absence_type.code,
                "request_kind": request.request_kind,
                "is_extension": request.request_kind == "EXTEND",
                "first_day": _day(request.requested_start_at),
                "last_day": _day(request.requested_end_at),
                "requires_document": request.absence_type.requires_document,
                "document": _document_json(row),
                "review_comment": request.review_comment,
                **hr_period_summary(request),
            },
        }

    def _document_event(self, row: AbsenceDocument) -> Event:
        request = row.absence_request
        return Event(
            type=TYPE_DOCUMENT,
            entity_id=row.id,
            created_at=row.created_at,
            title="Загружена справка",
            short_text=request.absence_type.name,
            status=row.verification_status,
            status_label=DOCUMENT_STATUS_LABELS.get(
                row.verification_status, row.verification_status
            ),
            priority="HIGH" if row.verification_status == "PENDING" else "NORMAL",
            requires_action=row.verification_status == "PENDING",
            related_entity_type="absence_requests",
            related_entity_id=request.id,
            action_url=f"/requests?request={request.id}",
            action_title="Открыть больничный",
            employee_id=request.employee_id,
            employee_name=_full_name(request.employee),
        )

    def _correction_detail(self, actor: Actor, entity_id: uuid.UUID):
        if not self.access.has(actor, "attendance.read"):
            raise NotFound("Событие не найдено")
        rows = self._sub(AttendanceHrService).correction_queue(actor)
        row = (
            rows.select_related("attendance_session", "employee")
            .filter(id=entity_id)
            .first()
        )
        if row is None:
            raise NotFound("Событие не найдено")
        event = self._correction_event(row)
        session = row.attendance_session
        return event, {
            "comment": row.reason,
            "author": _person_by_id(row.reviewed_by_user_id),
            "occurred_at": row.submitted_at,
            "correction": {
                "day": _day(
                    session.started_at if session else row.requested_entry_at
                ),
                "current_entry_at": session.started_at if session else None,
                "current_exit_at": session.ended_at if session else None,
                "requested_entry_at": row.requested_entry_at,
                "requested_exit_at": row.requested_exit_at,
                "event_kind": _correction_kind(row),
                "review_comment": row.review_comment,
                # Документа у исправления отметки в схеме нет: сотрудник
                # объясняет причину текстом. Поля «приложен документ»
                # здесь не будет, пока не будет самого документа.
                "has_document": False,
            },
        }

    def _session_detail(self, actor: Actor, entity_id: uuid.UUID):
        if not self.access.has(actor, "attendance.read"):
            raise NotFound("Событие не найдено")
        page = self._sub(AttendanceHrService).sessions(
            actor, only_open=True, limit=SOURCE_LIMIT
        )
        row = next((one for one in page.items if one.id == entity_id), None)
        if row is None:
            raise NotFound("Событие не найдено")
        now = timezone.now()
        event = self._session_event(row, now)
        entry = (
            AttendanceSession.objects.select_related(
                "entry_event__qr_point", "office"
            )
            .filter(id=row.id)
            .first()
        )
        qr_point = (
            entry.entry_event.qr_point
            if entry and entry.entry_event_id and entry.entry_event.qr_point_id
            else None
        )
        return event, {
            "comment": None,
            "author": None,
            "occurred_at": row.started_at,
            "session": {
                "started_at": row.started_at,
                "open_minutes": int((now - row.started_at).total_seconds() // 60),
                "office_name": row.office.name if row.office_id else None,
                "schedule_name": _schedule_name(row.employee_id, row.started_at.date()),
                "qr_point_name": qr_point.name if qr_point else None,
                "last_event_at": (
                    entry.entry_event.occurred_at
                    if entry and entry.entry_event_id
                    else None
                ),
                "last_event_type": (
                    entry.entry_event.event_type
                    if entry and entry.entry_event_id
                    else None
                ),
            },
        }

    def _question_detail(self, actor: Actor, entity_id: uuid.UUID):
        if not self.access.has(actor, "questions.read"):
            raise NotFound("Событие не найдено")
        row = (
            self._scoped(actor, EmployeeQuestion.objects.select_related("employee"))
            .filter(id=entity_id)
            .first()
        )
        if row is None:
            raise NotFound("Событие не найдено")
        event = self._question_event(row)
        return event, {
            "comment": _snippet(row.question_text, 400),
            "author": _person_by_id(row.assigned_to_user_id),
            "occurred_at": row.created_at,
            "question": {
                "topic": row.normalized_topic,
                "channel": row.channel,
                "category": row.category,
                "priority": row.priority,
                "last_message_at": row.last_message_at,
                "assigned_to": _person_by_id(row.assigned_to_user_id),
            },
        }

    def _question_event(self, row: EmployeeQuestion) -> Event:
        return Event(
            type=TYPE_QUESTION,
            entity_id=row.id,
            created_at=row.created_at,
            title="Новое обращение",
            short_text=_snippet(row.normalized_topic or row.question_text, SNIPPET),
            status=row.status,
            status_label=QUESTION_STATUS_LABELS.get(row.status, row.status),
            priority=(
                "CRITICAL" if row.priority == "URGENT"
                else "HIGH" if row.status != "CLOSED"
                else "NORMAL"
            ),
            requires_action=row.status in ("NEW", "IN_PROGRESS"),
            related_entity_type="employee_questions",
            related_entity_id=row.id,
            action_url=f"/questions?id={row.id}",
            action_title="Открыть обращение",
            employee_id=row.employee_id,
            employee_name=_full_name(row.employee),
        )

    def _delivery_detail(self, actor: Actor, entity_id: uuid.UUID):
        if not self.access.has(actor, "notifications.read"):
            raise NotFound("Событие не найдено")
        row = (
            self._scoped(
                actor, Notification.objects.select_related("employee")
            )
            .filter(id=entity_id, status="FAILED")
            .first()
        )
        if row is None:
            raise NotFound("Событие не найдено")
        event = Event(
            type=TYPE_DELIVERY,
            entity_id=row.id,
            created_at=row.updated_at,
            title="Ошибка доставки сообщения",
            short_text=_snippet(row.title or row.notification_type, SNIPPET),
            status=row.status,
            status_label="Не доставлено",
            priority="CRITICAL",
            requires_action=True,
            related_entity_type="notifications",
            related_entity_id=row.id,
            action_url=f"/notifications?view=delivery&id={row.id}",
            action_title="Открыть историю отправки",
            employee_id=row.employee_id,
            employee_name=_full_name(row.employee),
        )
        last = row.attempt_log.order_by("-attempted_at", "-number").first()
        return event, {
            # Причина — короткий код из очереди, а не ответ провайдера:
            # в ответе бывает эхо запроса, то есть текст сообщения.
            "comment": row.error_message,
            "author": None,
            "occurred_at": row.updated_at,
            "delivery": {
                "channel": row.channel,
                "notification_type": row.notification_type,
                "attempts": row.attempts,
                "last_attempt_at": (last.attempted_at if last else row.updated_at),
                "next_attempt_at": row.next_attempt_at,
                "will_retry": row.next_attempt_at is not None,
            },
        }

    def _report_detail(self, actor: Actor, entity_id: uuid.UUID):
        row = ExportJob.objects.filter(
            id=entity_id,
            organization_id=actor.organization_id,
            requested_by_user_id=actor.user_id,
            status="SUCCEEDED",
        ).first()
        if row is None:
            raise NotFound("Событие не найдено")
        event = Event(
            type=TYPE_REPORT,
            entity_id=row.id,
            created_at=row.finished_at or row.created_at,
            title="Отчёт готов",
            short_text=f"{row.kind} · {row.fmt.upper()}",
            status=row.status,
            status_label="Готов",
            priority="NORMAL",
            requires_action=False,
            related_entity_type="export_jobs",
            related_entity_id=row.id,
            action_url="/reports",
            action_title="Открыть отчёты",
        )
        return event, {
            "comment": None,
            "author": _person_by_id(row.requested_by_user_id),
            "occurred_at": row.finished_at or row.created_at,
            "report": {
                "kind": row.kind,
                "fmt": row.fmt,
                "file_name": row.file_name,
                "size_bytes": row.size_bytes,
                "expires_at": row.expires_at,
                "rows": row.total_rows,
            },
        }

    def _employee_detail(self, actor: Actor, entity_id: uuid.UUID):
        if not self.access.has(actor, "employees.read"):
            raise NotFound("Событие не найдено")
        row = (
            self._scoped(actor, Employee.objects.all(), own=True)
            .filter(id=entity_id)
            .first()
        )
        if row is None:
            raise NotFound("Событие не найдено")
        event = Event(
            type=TYPE_EMPLOYEE,
            entity_id=row.id,
            created_at=row.created_at,
            title="Новый сотрудник добавлен",
            short_text=f"Табельный номер {row.employee_number}",
            status=row.employment_status,
            status_label=(
                "Работает" if row.employment_status == "ACTIVE"
                else row.employment_status
            ),
            priority="NORMAL",
            requires_action=False,
            related_entity_type="employees",
            related_entity_id=row.id,
            action_url=f"/employees/{row.id}",
            action_title="Открыть карточку сотрудника",
            employee_id=row.id,
            employee_name=_full_name(row),
        )
        return event, {
            "comment": None,
            "author": None,
            "occurred_at": row.created_at,
            "new_employee": {
                "employee_number": row.employee_number,
                "hire_date": row.hire_date,
                "employment_status": row.employment_status,
                "telegram_connected": row.telegram_connected,
            },
        }

    # ------------------------------------------------------------------ внутри

    def _absence_queue(self, actor: Actor):
        """Очередь заявок с её собственными правами и областью.

        Возвращает None, если права на отсутствия нет: источник просто
        не даёт строк. Отказ здесь неуместен — лента общая.
        """
        if not self.access.has(actor, "absences.read"):
            return None
        return self._sub(AbsenceService).queue(actor)

    def _sub(self, service_class):
        """Соседний сервис с ОБЩИМ кэшем прав.

        Каждый источник спрашивает разрешения и область по-своему, но
        отвечать они обязаны одинаково и на один момент времени. Общий
        `AccessControl` заодно избавляет от восьми одинаковых запросов
        к ролям на каждую сборку ленты.
        """
        service = service_class()
        service.access = self.access
        return service

    def _scoped(self, actor: Actor, queryset, *, own: bool = False):
        """Ограничение выборки офисами, доступными пользователю.

        `own=True` — у строки собственный `id` сотрудника (карточка
        сотрудника), иначе сотрудник указан полем `employee_id`.

        Область берётся по ЛЮБОМУ периоду назначения, как и в остальных
        сервисах: после перевода человека его прошлые события не должны
        исчезать у того, кто их разбирал.
        """
        queryset = queryset.filter(organization_id=actor.organization_id)
        visible = self.access.visible_office_ids(actor)
        if visible is None:
            return queryset
        # Пустое множество — «не видно ничего», а не «видно всё».
        employees = EmployeeAssignment.objects.filter(
            office_id__in=visible
        ).values_list("employee_id", flat=True)
        field = "id__in" if own else "employee_id__in"
        return queryset.filter(**{field: employees})

    def _places(self, employee_ids: set[uuid.UUID]) -> dict:
        """Офис, отдел и должность по действующему назначению — одним запросом."""
        if not employee_ids:
            return {}
        rows = EmployeeAssignment.objects.filter(
            current_primary_assignment_filter(date.today()),
            employee_id__in=employee_ids,
        ).select_related("office", "department", "position")
        return {
            row.employee_id: {
                "office_id": row.office_id,
                "office_name": row.office.name if row.office_id else None,
                "department_name": row.department.name if row.department_id else None,
                "position_name": row.position.name if row.position_id else None,
            }
            for row in rows
        }

    @staticmethod
    def _known_scope(scope: str | None) -> str:
        allowed = {key for key, _ in FILTERS}
        wanted = scope or "all"
        if wanted not in allowed:
            raise ValidationFailed(
                "Неизвестный фильтр ленты",
                details={"field": "scope", "value": scope,
                         "allowed": sorted(allowed)},
            )
        return wanted

    @staticmethod
    def _parse(event_id: str) -> tuple[str, uuid.UUID]:
        """Разбор `вид:запись`. Испорченный ключ — это 400, а не 500."""
        raw = (event_id or "").split(":", 1)
        if len(raw) != 2 or raw[0] not in GROUPS:
            raise ValidationFailed(
                "Некорректный идентификатор события",
                details={"field": "id", "value": event_id},
            )
        try:
            return raw[0], uuid.UUID(raw[1])
        except ValueError as exc:
            raise ValidationFailed(
                "Некорректный идентификатор события",
                details={"field": "id", "value": event_id},
            ) from exc


def _document_json(row: AbsenceDocument | None) -> dict | None:
    """Безопасные сведения о файле: имя, размер, дата, проверка.

    Самого документа здесь нет и быть не может: в ленте показывают, что
    справка есть, а не что в ней написано.
    """
    if row is None:
        return None
    return {
        "id": str(row.id),
        "document_type": row.document_type,
        "verification_status": row.verification_status,
        "verification_label": DOCUMENT_STATUS_LABELS.get(
            row.verification_status, row.verification_status
        ),
        "verified_at": row.verified_at,
        "file_name": row.file.original_filename,
        "size_bytes": row.file.size_bytes,
        "uploaded_at": row.file.created_at,
        "scan_status": row.file.scan_status,
    }


def _schedule_name(employee_id: uuid.UUID, day: date) -> str | None:
    from humotech.schedules.models import EmployeeScheduleAssignment

    row = (
        EmployeeScheduleAssignment.objects.filter(
            employee_id=employee_id, valid_from__lte=day
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=day))
        .select_related("schedule")
        .order_by("-valid_from")
        .first()
    )
    return row.schedule.name if row else None


def _person_by_id(user_id) -> dict | None:
    """Кто сделал: ФИО сотрудника, а без карточки — адрес учётной записи."""
    if user_id is None:
        return None
    from humotech.accounts.models import User

    user = User.objects.select_related("employee").filter(id=user_id).first()
    if user is None:
        return None
    name = _full_name(user.employee) if user.employee_id else None
    return {"id": str(user.id), "name": name or user.email}


def _period_text(row: AbsenceRequest) -> str:
    first = _day(row.requested_start_at)
    last = _day(row.requested_end_at)
    if first is None or last is None:
        return row.absence_type.name
    days = (last - first).days + 1
    return f"{row.absence_type.name}, {days} дн."


def _correction_text(row) -> str:
    kind = _correction_kind(row)
    at = row.requested_entry_at or row.requested_exit_at
    if at is None:
        return "Исправление отметки"
    return f"{kind}, {_clock(at)}"


def _correction_kind(row) -> str:
    if row.requested_entry_at and row.requested_exit_at:
        return "Вход и выход"
    return "Вход" if row.requested_entry_at else "Выход"


def _full_name(employee) -> str:
    if employee is None:
        return ""
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


def _snippet(text: str | None, limit: int) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _day(moment: datetime | None) -> date | None:
    return moment.date() if moment else None


def _clock(moment: datetime) -> str:
    return timezone.localtime(moment).strftime("%H:%M")


__all__ = [
    "FEED_DAYS",
    "FEED_PRIORITIES",
    "FEED_TYPES",
    "FILTERS",
    "GROUPS",
    "Event",
    "FeedService",
    "event_json",
]
