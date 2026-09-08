"""Посещаемость глазами кадровика: кто где, что было и что исправили.

Отдельный модуль от `services.py` намеренно. Там — приём отметки от
сотрудника: одна транзакция, одна блокировка, один человек. Здесь —
чтение по многим людям сразу, и требования у этих задач разные вплоть
до противоположных.

Три решения, которые здесь приняты раз и навсегда.

**Сырое событие не меняется.** `AttendanceEvent` не имеет `updated_at`
и не редактируется ни одним методом этого модуля. Исправление кадровика —
это НОВАЯ строка: либо `AttendanceEvent` с `source = MANUAL`, либо
решение по `AttendanceCorrectionRequest`. Прежнее значение остаётся
в базе навсегда. Иначе спор «во сколько человек пришёл» разрешить нечем:
единственная запись, которую можно переписать, — это не доказательство.

**Границы суток — в поясе офиса.** Не сервера и не кадровика. «Кто сегодня
в офисе» в Худжанде и в Душанбе — разные вопросы с разными ответами, и
складывать их по московскому времени бессмысленно.

**Сессия принадлежит дню своего начала** — то же правило, что и в
`statistics.py`, и по той же причине. Ночная смена целиком относится
к дню, когда началась.

Область видимости проверяется на КАЖДОМ запросе и всегда через
`AccessControl`: пустая область означает «ничего», а не «всё».
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import Q, QuerySet

from humotech.absences.models import EmployeeAbsence
from humotech.attendance.models import (
    AttendanceCorrectionRequest,
    AttendanceEvent,
    AttendanceSession,
)
from humotech.attendance.statistics import (
    COUNTED_ABSENCE_STATUSES,
    SICK_CODES,
    VACATION_CODES,
)
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor
from humotech.core.service import BaseService
from humotech.core.timeframes import day_bounds, office_zone, range_bounds
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.services import current_primary_assignment_filter
from humotech.offices.models import Office
from humotech.schedules.models import CalendarException, EmployeeScheduleAssignment

# Состояния человека на выбранный день. Порядок важен: он же задаёт
# приоритет, если признаков сразу несколько.
#
# Отсутствие сильнее присутствия намеренно. Человек на больничном,
# приложивший пропуск, — это повод разобраться, а не тихо посчитать его
# «пришедшим»: либо больничный оформлен задним числом, либо отметку сделал
# не он. В обоих случаях кадровик должен увидеть предупреждение, а не
# ровную строку.
#: Сколько дней помещается в один журнал по человеку.
#:
#: Журнал спрашивает график и календарь на КАЖДЫЙ день отдельно —
#: иначе пришлось бы повторить их правила второй раз и в другом
#: месте. Месяц с запасом — предел, за которым это перестаёт быть
#: карточкой и становится отчётом; для длинных периодов есть
#: выгрузка и `/analytics`.
MAX_JOURNAL_DAYS = 31

PRESENCE_STATES = (
    "SICK_LEAVE",
    "VACATION",
    "OTHER_ABSENCE",
    "IN_OFFICE",
    "LEFT",
    "NOT_COME",
    "DAY_OFF",
    "NO_SCHEDULE",
)


@dataclass(frozen=True)
class PresenceRow:
    """Один человек на выбранный день."""

    employee_id: uuid.UUID
    full_name: str
    employee_number: str | None
    office_id: uuid.UUID | None
    office_name: str | None
    department_name: str | None
    position_name: str | None

    state: str
    first_entry_at: datetime | None
    last_exit_at: datetime | None
    seconds: int
    open_session_id: uuid.UUID | None

    # Опоздание. `None` — не «не опоздал», а «сравнивать не с чем»:
    # графика нет или человек не пришёл. Ноль минут означал бы, что
    # пришёл ровно вовремя, а это другое утверждение.
    late_minutes: int | None
    scheduled_start: time | None

    absence_code: str | None
    absence_name: str | None
    # Отметка есть, хотя человек числится отсутствующим. Не ошибка сама
    # по себе — повод посмотреть.
    conflicting_marks: bool


@dataclass(frozen=True)
class PresenceReport:
    day: date
    timezone: str
    #: Полный набор: по нему считает дашборд и по нему же строится
    #: выгрузка. Потолок на размер ответа живёт в HTTP-слое, а не здесь,
    #: — обрезать данные до того, как их посчитали, значит занизить
    #: и карточки, и файл.
    rows: list[PresenceRow]

    def counts(self) -> dict[str, int]:
        """Сколько человек в каждом состоянии — для карточек дашборда."""
        totals = {state: 0 for state in PRESENCE_STATES}
        for row in self.rows:
            totals[row.state] = totals.get(row.state, 0) + 1
        return totals


class AttendanceHrService(BaseService):
    """Чтение посещаемости и работа с исправлениями.

    Требует `attendance.read` для чтения, `attendance.correct` для решений
    по заявкам и `attendance.manual` для ручной отметки.
    """

    # ------------------------------------------------------------ присутствие

    def presence(
        self,
        actor: Actor,
        *,
        day: date | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        position_id: uuid.UUID | None = None,
        schedule_id: uuid.UUID | None = None,
        state: str | None = None,
        search: str | None = None,
    ) -> PresenceReport:
        """Кто где на выбранный день.

        Постраничного вывода здесь нет намеренно: по этому ответу
        считаются карточки дашборда и строится выгрузка, а итог по первым
        пятидесяти строкам — не итог. Набор возвращается целиком.

        Потолок на размер HTTP-ответа есть, но живёт он во view
        (`PRESENCE_MAX_ROWS`): обрезать здесь значило бы занизить
        и карточки, и файл выгрузки.
        """
        self.access.require(actor, "attendance.read")
        if state and state not in PRESENCE_STATES:
            raise ValidationFailed(
                "Неизвестное состояние присутствия",
                details={"state": state, "allowed": list(PRESENCE_STATES)},
            )

        offices = self._offices_in_scope(
            actor, office_id=office_id, region_id=region_id
        )
        # Пояс берётся у офиса. Если офисов несколько и пояса разные,
        # берётся пояс первого — и он же называется в ответе, чтобы клиент
        # не гадал, по какому времени посчитан день.
        tz = office_zone(offices[0]) if offices else office_zone(None)
        day = day or date.today()
        start, end = day_bounds(day, tz)

        rows = self._roster(
            actor,
            offices=offices,
            at=day,
            department_id=department_id,
            position_id=position_id,
            schedule_id=schedule_id,
            search=search,
        )
        if not rows:
            return PresenceReport(day=day, timezone=str(tz), rows=[])

        employee_ids = list(rows.keys())
        sessions = self._sessions_of_day(employee_ids, start, end)
        absences = self._absences_of_day(employee_ids, start, end)
        scheduled, calendar = self._scheduled_starts(
            employee_ids,
            day,
            organization_id=actor.organization_id,
            office_ids=[office.id for office in offices],
        )

        result = [
            self._presence_row(
                assignment=assignment,
                day=day,
                tz=tz,
                sessions=sessions.get(employee_id, []),
                absence=absences.get(employee_id),
                schedule=scheduled.get(employee_id),
                calendar=calendar,
            )
            for employee_id, assignment in rows.items()
        ]
        result.sort(key=lambda row: row.full_name)
        if state:
            result = [row for row in result if row.state == state]
        return PresenceReport(day=day, timezone=str(tz), rows=result)

    # ------------------------------------------------- журнал одного человека

    def daily(
        self,
        actor: Actor,
        employee_id: uuid.UUID,
        *,
        first: date,
        last: date,
    ) -> dict:
        """День за днём по одному сотруднику — и итоги за ВЕСЬ период.

        Считает тот же код, что и `presence()`: строка дня собирается
        `_presence_row`, сессии берутся `_sessions_of_day`, отсутствия —
        `_absences_of_day`, график и календарь — `_scheduled_starts`.
        Второй реализации тех же правил здесь нет намеренно: у ночной
        смены, открытой сессии и допуска опоздания должен быть один
        ответ, а не два похожих.

        Три правила, которые из-за этого достаются журналу даром и
        которые легко потерять, считая на клиенте:

          * **день определяется поясом ОФИСА**, а не браузера и не
            организации. Перевод в офис с другим поясом посреди периода
            меняет пояс со дня перевода — назначение берётся на каждый
            день своё;
          * **ночная смена принадлежит дню, в который НАЧАЛАСЬ.** Так
            её считает `_sessions_of_day`, и журнал обязан совпадать
            с присутствием, а не спорить с ним;
          * **открытая сессия учитывается до момента расчёта.** Её
            длительность живёт в `duration_seconds`, который
            пересчитывает сервер; клиент, вычитающий «сейчас минус
            вход», получил бы другое число.

        Итоги считаются по всему периоду, а не по показанным строкам:
        сводка, зависящая от длины таблицы, отвечает не на тот вопрос.
        """
        self.access.require(actor, "attendance.read")
        # Проверка области: чужой сотрудник отвечает «не найден», а не
        # «нельзя» — иначе перебором идентификаторов считается чужой штат.
        self._require_employee_visible(actor, employee_id)

        if last < first:
            raise ValidationFailed(
                "Конец периода раньше начала",
                details={"date_from": first.isoformat(),
                         "date_to": last.isoformat()},
            )
        span = (last - first).days + 1
        if span > MAX_JOURNAL_DAYS:
            raise ValidationFailed(
                f"Журнал отдаётся не длиннее {MAX_JOURNAL_DAYS} дней",
                details={"days": span, "max_days": MAX_JOURNAL_DAYS},
            )

        # Назначения, накрывающие период, — одним запросом. Их может быть
        # несколько: человека переводили, и у каждого дня свой офис.
        assignments = list(
            EmployeeAssignment.objects.filter(
                employee_id=employee_id,
                is_primary=True,
                valid_from__lte=last,
            )
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=first))
            .select_related("employee", "office", "department", "position")
            .order_by("-valid_from")
        )
        if not assignments:
            # Без назначения нет ни офиса, ни пояса, ни области доступа.
            # Это не ошибка: у только что заведённого человека так и есть.
            return {
                "employee_id": employee_id,
                "timezone": str(office_zone(None)),
                "first": first,
                "last": last,
                "days": [],
                "totals": _empty_totals(),
                "note": "У сотрудника нет кадрового назначения за этот период",
            }

        # Область проверяется по каждому офису периода: перевод не должен
        # открывать чужой офис задним числом.
        for assignment in assignments:
            if assignment.office_id:
                self.access.require_office(actor, assignment.office_id)

        rows: list[dict] = []
        totals = _empty_totals()
        shown_zone = None

        for offset in range(span):
            day = first + timedelta(days=offset)
            assignment = _assignment_on(assignments, day)
            if assignment is None:
                continue
            tz = office_zone(assignment.office)
            if shown_zone is None:
                shown_zone = str(tz)
            start, end = day_bounds(day, tz)

            sessions = self._sessions_of_day([employee_id], start, end).get(
                employee_id, []
            )
            absence = self._absences_of_day([employee_id], start, end).get(
                employee_id
            )
            scheduled, calendar = self._scheduled_starts(
                [employee_id],
                day,
                organization_id=actor.organization_id,
                office_ids=[assignment.office_id] if assignment.office_id else [],
            )
            row = self._presence_row(
                assignment=assignment,
                day=day,
                tz=tz,
                sessions=sessions,
                absence=absence,
                schedule=scheduled.get(employee_id),
                calendar=calendar,
            )
            open_session = row.open_session_id is not None
            rows.append(
                {
                    "day": day,
                    "timezone": str(tz),
                    "office_id": row.office_id,
                    "office_name": row.office_name,
                    "state": row.state,
                    "first_entry_at": row.first_entry_at,
                    "last_exit_at": row.last_exit_at,
                    "seconds": row.seconds,
                    "sessions": len(sessions),
                    "open_session_id": row.open_session_id,
                    "late_minutes": row.late_minutes,
                    "scheduled_start": row.scheduled_start,
                    "absence_code": row.absence_code,
                    "absence_name": row.absence_name,
                    # Отметки в день подтверждённого отсутствия — не норма,
                    # и молчать об этом нельзя: расхождение разбирает человек.
                    "conflicting_marks": row.conflicting_marks,
                }
            )

            totals["seconds"] += row.seconds
            if sessions:
                totals["days_with_marks"] += 1
            if open_session:
                totals["open_sessions"] += 1
            if row.scheduled_start is not None:
                totals["working_days"] += 1
            if row.late_minutes:
                totals["late_days"] += 1
                totals["late_minutes"] += row.late_minutes

        return {
            "employee_id": employee_id,
            "timezone": shown_zone or str(office_zone(None)),
            "first": first,
            "last": last,
            "days": rows,
            "totals": totals,
            "note": None,
        }

    # ----------------------------------------------------------------- события

    def events(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        event_type: str | None = None,
        source: str | None = None,
        verification_status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """Журнал сканирований, включая отклонённые.

        Только чтение. Метода изменения или удаления события в этом сервисе
        нет и не должно появиться.
        """
        self.access.require(actor, "attendance.read")

        queryset = AttendanceEvent.objects.filter(
            organization_id=actor.organization_id
        ).select_related("employee", "office", "qr_point")

        queryset = self._limit_to_scope(
            actor, queryset, office_id=office_id, region_id=region_id
        )
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        if event_type:
            queryset = queryset.filter(event_type=event_type)
        if source:
            queryset = queryset.filter(source=source)
        if verification_status:
            queryset = queryset.filter(verification_status=verification_status)
        queryset = self._limit_to_period(
            queryset, "occurred_at", date_from, date_to, actor
        )
        return paginate(queryset, limit=limit, cursor=cursor)

    def sessions(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
        only_open: bool = False,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """Рабочие сессии. `only_open=True` — незакрытые."""
        self.access.require(actor, "attendance.read")

        queryset = AttendanceSession.objects.filter(
            organization_id=actor.organization_id
        ).select_related("employee", "office")

        queryset = self._limit_to_scope(
            actor, queryset, office_id=office_id, region_id=region_id
        )
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        if status:
            queryset = queryset.filter(status=status)
        if only_open:
            # Именно по `ended_at`, а не только по статусу: статус может
            # быть выставлен пересчётом, а незакрытой сессию делает
            # отсутствие выхода.
            queryset = queryset.filter(ended_at__isnull=True)
        queryset = self._limit_to_period(
            queryset, "started_at", date_from, date_to, actor
        )
        return paginate(queryset, limit=limit, cursor=cursor)

    # ------------------------------------------------------------ исправления

    def correction_queue(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        employee_id: uuid.UUID | None = None,
        office_id=None,
        region_id=None,
        search: str | None = None,
    ):
        """Заявки на исправление отметок без страницы.

        Нужна общей очереди HR: она сама сливает два потока и режет их
        одним курсором. Правила доступа те же, что у `corrections`.
        """
        self.access.require(actor, "attendance.read")

        queryset = AttendanceCorrectionRequest.objects.filter(
            organization_id=actor.organization_id
        ).select_related("employee")

        if status:
            queryset = queryset.filter(status__in=[s for s in status.split(",") if s])
        if employee_id:
            # Фильтр сужает уже разрешённое, а не открывает доступ:
            # проверка области ниже остаётся на месте.
            self._require_employee_visible(actor, employee_id)
            queryset = queryset.filter(employee_id=employee_id)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(
                Q(employee__first_name__icontains=pattern)
                | Q(employee__last_name__icontains=pattern)
                | Q(employee__employee_number__icontains=pattern)
            )

        visible = self._employee_scope_ids(
            actor, office_id=office_id, region_id=region_id
        )
        if visible is not None:
            queryset = queryset.filter(employee_id__in=visible)
        return queryset

    def corrections(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        employee_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "attendance.read")

        queryset = AttendanceCorrectionRequest.objects.filter(
            organization_id=actor.organization_id
        ).select_related("employee", "attendance_session", "reviewed_by_user")

        if status:
            queryset = queryset.filter(status=status)
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)

        visible = self._employee_scope_ids(
            actor, office_id=office_id, region_id=region_id
        )
        if visible is not None:
            queryset = queryset.filter(employee_id__in=visible)
        return paginate(queryset, limit=limit, cursor=cursor)

    def review_correction(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        *,
        decision: str,
        comment: str | None = None,
    ) -> AttendanceCorrectionRequest:
        """Решение по заявке на исправление.

        Само событие при этом не переписывается. Одобренная заявка меняет
        РАСЧЁТНУЮ сессию и остаётся в базе как основание: кто решил, когда
        и почему. Что было до исправления, видно по событиям, которых
        никто не трогал.
        """
        if decision not in ("approve", "reject"):
            raise ValidationFailed(
                "Решение может быть только approve или reject",
                details={"decision": decision},
            )
        self.access.require(actor, "attendance.correct")

        with self.atomic():
            try:
                # Без `select_related` на сессию: она необязательна, join
                # получается внешним, а `FOR UPDATE` к его правой стороне
                # PostgreSQL не применяет. Блокируется именно заявка —
                # ровно та строка, из-за которой возможна гонка двух
                # одновременных решений.
                request = AttendanceCorrectionRequest.objects.select_for_update().get(
                    id=request_id, organization_id=actor.organization_id
                )
            except AttendanceCorrectionRequest.DoesNotExist as exc:
                raise NotFound("Заявка на исправление не найдена") from exc

            self._require_employee_visible(actor, request.employee_id)

            if request.status not in ("SUBMITTED", "IN_REVIEW"):
                # Повторное решение по уже рассмотренной заявке — не ошибка
                # сети, а попытка изменить принятое решение. Здесь она
                # отклоняется: отменять решение нужно отдельной операцией
                # с собственным следом в журнале.
                raise Conflict(
                    "Заявка уже рассмотрена",
                    details={"status": request.status},
                )

            before = {
                "status": request.status,
                "requested_entry_at": _iso(request.requested_entry_at),
                "requested_exit_at": _iso(request.requested_exit_at),
            }

            request.status = "APPROVED" if decision == "approve" else "REJECTED"
            request.reviewed_by_user_id = actor.user_id
            request.reviewed_at = _now()
            request.review_comment = (comment or "").strip() or None
            request.save(
                update_fields=[
                    "status",
                    "reviewed_by_user",
                    "reviewed_at",
                    "review_comment",
                    "updated_at",
                ]
            )

            session_after = None
            if decision == "approve" and request.attendance_session_id:
                session_after = self._apply_correction(request)

            self.audit.record(
                actor,
                action="attendance.correction.reviewed",
                entity_type="attendance_correction_requests",
                entity_id=request.id,
                before=before,
                after={
                    "status": request.status,
                    "review_comment": request.review_comment,
                    "session": session_after,
                },
            )
        return request

    def manual_event(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID,
        office_id: uuid.UUID,
        event_type: str,
        occurred_at: datetime,
        reason: str,
    ) -> AttendanceEvent:
        """Ручная отметка кадровика.

        Это ДОБАВЛЕНИЕ события, а не правка существующего: `source = MANUAL`
        отличает её от сканирования навсегда, а причина обязательна. Молчаливой
        ручной отметки без основания в системе быть не может — по ней потом
        считают рабочее время.
        """
        if event_type not in ("ENTRY", "EXIT"):
            raise ValidationFailed(
                "Тип события может быть только ENTRY или EXIT",
                details={"event_type": event_type},
            )
        reason = (reason or "").strip()
        if not reason:
            raise ValidationFailed(
                "Причина ручной отметки обязательна", details={"field": "reason"}
            )
        self.access.require(actor, "attendance.manual")
        self.access.require_office(actor, office_id)
        self._require_employee_visible(actor, employee_id)

        with self.atomic():
            event = AttendanceEvent.objects.create(
                organization_id=actor.organization_id,
                employee_id=employee_id,
                office_id=office_id,
                event_type=event_type,
                source="MANUAL",
                verification_status="ACCEPTED",
                occurred_at=occurred_at,
                event_metadata={
                    "reason": reason,
                    "created_by_user_id": str(actor.user_id),
                },
            )
            self.audit.record(
                actor,
                action="attendance.event.manual",
                entity_type="attendance_events",
                entity_id=event.id,
                before=None,
                after={
                    "employee_id": str(employee_id),
                    "office_id": str(office_id),
                    "event_type": event_type,
                    "occurred_at": occurred_at.isoformat(),
                    "reason": reason,
                },
            )
        return event

    # ------------------------------------------------------------ внутреннее

    def _offices_in_scope(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID | None,
        region_id: uuid.UUID | None,
    ) -> list[Office]:
        queryset = Office.objects.filter(organization_id=actor.organization_id)
        visible = self.access.office_filter(actor)
        if visible is not None:
            queryset = queryset.filter(visible)
        if office_id:
            self.access.require_office(actor, office_id)
            queryset = queryset.filter(id=office_id)
        if region_id:
            self.access.require_region(actor, region_id)
            queryset = queryset.filter(region_id=region_id)
        return list(queryset.select_related("region").order_by("name"))

    def _employee_scope_ids(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        at: date | None = None,
    ) -> QuerySet | None:
        """Идентификаторы сотрудников в области видимости. None — все.

        Возвращается именно `None` для «всей организации» и ПУСТОЙ набор
        для «ничего не видно». Слить эти случаи проверкой `if ids:` —
        значит молча показать всю организацию тому, у кого прав нет.
        """
        condition = None
        if office_id:
            self.access.require_office(actor, office_id)
            condition = Q(office_id=office_id)
        elif region_id:
            self.access.require_region(actor, region_id)
            condition = Q(office__region_id=region_id)
        else:
            visible = self.access.visible_office_ids(actor)
            if visible is not None:
                condition = Q(office_id__in=visible)

        if condition is None:
            return None
        return EmployeeAssignment.objects.filter(
            current_primary_assignment_filter(at or date.today()) & condition
        ).values_list("employee_id", flat=True)

    def _limit_to_scope(
        self,
        actor: Actor,
        queryset: QuerySet,
        *,
        office_id: uuid.UUID | None,
        region_id: uuid.UUID | None,
    ) -> QuerySet:
        """Ограничение по офису самого события, а не по офису сотрудника.

        Событие произошло в конкретном офисе, и видеть его должен тот, кому
        этот офис доступен. Фильтровать по текущему офису сотрудника было бы
        неверно: после перевода человека его прошлые отметки перестали бы
        быть видны администратору офиса, где они и сделаны.
        """
        if office_id:
            self.access.require_office(actor, office_id)
            return queryset.filter(office_id=office_id)
        if region_id:
            self.access.require_region(actor, region_id)
            return queryset.filter(office__region_id=region_id)
        visible = self.access.visible_office_ids(actor)
        if visible is None:
            return queryset
        return queryset.filter(office_id__in=visible)

    def _limit_to_period(
        self,
        queryset: QuerySet,
        field: str,
        date_from: date | None,
        date_to: date | None,
        actor: Actor,
    ) -> QuerySet:
        if not date_from and not date_to:
            return queryset
        if date_from and date_to and date_to < date_from:
            raise ValidationFailed(
                "Конец периода раньше начала",
                details={"date_from": date_from.isoformat(),
                         "date_to": date_to.isoformat()},
            )
        # Пояс организации: у списка может не быть одного офиса, а границы
        # суток нужны определённые. Названы они в ответе рядом с данными.
        tz = self._organization_zone(actor)
        first = date_from or date_to
        last = date_to or date_from
        start, end = range_bounds(first, last, tz)
        return queryset.filter(**{f"{field}__gte": start, f"{field}__lt": end})

    def _organization_zone(self, actor: Actor):
        office = (
            Office.objects.filter(organization_id=actor.organization_id)
            .exclude(timezone="")
            .order_by("created_at")
            .first()
        )
        return office_zone(office)

    def _require_employee_visible(self, actor: Actor, employee_id) -> None:
        visible = self._employee_scope_ids(actor)
        if visible is None:
            exists = Employee.objects.filter(
                id=employee_id, organization_id=actor.organization_id
            ).exists()
        else:
            exists = Employee.objects.filter(
                id=employee_id,
                organization_id=actor.organization_id,
                id__in=visible,
            ).exists()
        if not exists:
            # Один и тот же ответ на «нет такого» и «не ваш»: иначе
            # перебором идентификаторов пересчитывается чужой штат.
            raise NotFound("Сотрудник не найден")

    def _roster(
        self,
        actor: Actor,
        *,
        offices: list[Office],
        at: date,
        department_id: uuid.UUID | None,
        position_id: uuid.UUID | None,
        schedule_id: uuid.UUID | None,
        search: str | None,
    ) -> dict[uuid.UUID, EmployeeAssignment]:
        if not offices:
            return {}

        assignments = EmployeeAssignment.objects.filter(
            current_primary_assignment_filter(at),
            office_id__in=[office.id for office in offices],
            employee__organization_id=actor.organization_id,
            employee__employment_status="ACTIVE",
        ).select_related("employee", "office", "department", "position")

        if department_id:
            assignments = assignments.filter(department_id=department_id)
        if position_id:
            assignments = assignments.filter(position_id=position_id)
        if search:
            pattern = search.strip()
            assignments = assignments.filter(
                Q(employee__first_name__icontains=pattern)
                | Q(employee__last_name__icontains=pattern)
                | Q(employee__middle_name__icontains=pattern)
                | Q(employee__employee_number__icontains=pattern)
            )
        if schedule_id:
            with_schedule = EmployeeScheduleAssignment.objects.filter(
                schedule_id=schedule_id,
                valid_from__lte=at,
            ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=at)).values_list(
                "employee_id", flat=True
            )
            assignments = assignments.filter(employee_id__in=with_schedule)

        return {row.employee_id: row for row in assignments}

    @staticmethod
    def _sessions_of_day(
        employee_ids: list[uuid.UUID], start: datetime, end: datetime
    ) -> dict[uuid.UUID, list[AttendanceSession]]:
        """Сессии дня одним запросом на всех, а не по запросу на человека."""
        grouped: dict[uuid.UUID, list[AttendanceSession]] = {}
        rows = (
            AttendanceSession.objects.filter(
                employee_id__in=employee_ids,
                started_at__gte=start,
                started_at__lt=end,
            )
            .exclude(status="INVALID")
            .order_by("started_at")
        )
        for row in rows:
            grouped.setdefault(row.employee_id, []).append(row)
        return grouped

    @staticmethod
    def _absences_of_day(
        employee_ids: list[uuid.UUID], start: datetime, end: datetime
    ) -> dict[uuid.UUID, EmployeeAbsence]:
        """Отсутствия, накрывающие выбранный день.

        Границы отсутствия хранятся моментами времени, а не датами, и
        сравнивать их с датой напрямую нельзя: при любом переходе через
        полночь разница врёт на сутки. Поэтому перекрытие считается
        по границам дня в поясе офиса — тем же способом, что и в
        `statistics.py`.
        """
        rows = (
            EmployeeAbsence.objects.filter(
                employee_id__in=employee_ids,
                status__in=COUNTED_ABSENCE_STATUSES,
                start_at__lt=end,
                end_at__gte=start,
            )
            .select_related("absence_type")
            .order_by("start_at")
        )
        result: dict[uuid.UUID, EmployeeAbsence] = {}
        for row in rows:
            # Первое по времени выигрывает: два наложившихся отсутствия —
            # это ошибка данных, и показывать надо одно, а не оба.
            result.setdefault(row.employee_id, row)
        return result

    @staticmethod
    def _scheduled_starts(
        employee_ids: list[uuid.UUID],
        day: date,
        *,
        organization_id: uuid.UUID,
        office_ids: list[uuid.UUID],
    ) -> tuple[dict[uuid.UUID, tuple[time | None, bool, int]], dict]:
        """Начало смены, признак рабочего дня и допустимое опоздание — по одному запросу на всех.

        Значение `(None, False)` означает «график есть, день нерабочий»;
        отсутствие ключа — «графика нет вовсе». Это разные вещи: во втором
        случае про опоздание сказать нечего, а в первом — есть что: человек
        и не должен был приходить.
        """
        weekday = day.isoweekday()
        rows = (
            EmployeeScheduleAssignment.objects.filter(
                employee_id__in=employee_ids,
                valid_from__lte=day,
            )
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=day))
            .select_related("schedule")
            .prefetch_related("schedule__days")
            .order_by("employee_id", "-valid_from")
        )

        # Праздники и переносы. Исключение офиса перекрывает общее по
        # организации — тем же правилом, что и в `statistics.py`.
        exceptions: dict[uuid.UUID | None, bool] = {}
        calendar = CalendarException.objects.filter(
            Q(office_id__in=office_ids) | Q(office__isnull=True),
            organization_id=organization_id,
            date=day,
            is_active=True,
        )
        for row in sorted(calendar, key=lambda r: r.office_id is not None):
            exceptions[row.office_id] = row.is_working_day

        result: dict[uuid.UUID, tuple[time | None, bool, int]] = {}
        for row in rows:
            if row.employee_id in result:
                continue  # берём самое позднее действующее назначение
            match = next(
                (d for d in row.schedule.days.all() if d.weekday == weekday),
                None,
            )
            working = bool(match and match.is_working_day)
            result[row.employee_id] = (
                match.start_time if match and working else None,
                working,
                row.schedule.late_grace_minutes or 0,
            )
        return result, exceptions

    @staticmethod
    def _presence_row(
        *,
        assignment: EmployeeAssignment,
        day: date,
        tz,
        sessions: list[AttendanceSession],
        absence: EmployeeAbsence | None,
        schedule: tuple[time | None, bool, int] | None,
        calendar: dict,
    ) -> PresenceRow:
        employee = assignment.employee
        first_entry = sessions[0].started_at if sessions else None
        open_session = next((s for s in sessions if s.ended_at is None), None)
        last_exit = None
        for candidate in sessions:
            if candidate.ended_at and (
                last_exit is None or candidate.ended_at > last_exit
            ):
                last_exit = candidate.ended_at
        seconds = sum(s.duration_seconds or 0 for s in sessions)

        scheduled_start, is_working, grace_minutes = (
            schedule or (None, False, 0)
        )
        has_schedule = schedule is not None
        # Исключение календаря сильнее графика: в праздник не приходят даже
        # те, у кого этот день по графику рабочий. Строка офиса конкретнее
        # общей по организации и поэтому проверяется первой.
        if assignment.office_id in calendar:
            is_working = calendar[assignment.office_id]
        elif None in calendar:
            is_working = calendar[None]
        if not is_working:
            scheduled_start = None

        state, absence_code, absence_name = _state_of(
            absence=absence,
            has_marks=bool(sessions),
            open_session=open_session is not None,
            has_schedule=has_schedule,
            is_working=is_working,
        )

        late_minutes = None
        if first_entry and scheduled_start:
            local_entry = first_entry.astimezone(tz)
            planned = datetime.combine(day, scheduled_start, tzinfo=tz)
            delta = int((local_entry - planned).total_seconds() // 60)
            # Допуск из графика вычитается, а не сравнивается порогом:
            # организация, разрешившая приходить на 15 минут позже, считает
            # опозданием минуты СВЕРХ допуска, а не всю разницу целиком.
            # При нулевом допуске формула не меняет ничего.
            late_minutes = max(delta - grace_minutes, 0)

        return PresenceRow(
            employee_id=employee.id,
            full_name=_full_name(employee),
            employee_number=employee.employee_number,
            office_id=assignment.office_id,
            office_name=assignment.office.name if assignment.office else None,
            department_name=(
                assignment.department.name if assignment.department else None
            ),
            position_name=assignment.position.name if assignment.position else None,
            state=state,
            first_entry_at=first_entry,
            last_exit_at=last_exit,
            seconds=seconds,
            open_session_id=open_session.id if open_session else None,
            late_minutes=late_minutes,
            scheduled_start=scheduled_start,
            absence_code=absence_code,
            absence_name=absence_name,
            conflicting_marks=bool(absence and sessions),
        )

    def _apply_correction(self, request: AttendanceCorrectionRequest) -> dict:
        """Перенести одобренные значения в расчётную сессию.

        Меняется СЕССИЯ — производная величина, которую и так пересчитывают.
        События, из которых она собрана, остаются нетронутыми, и по ним
        всегда видно, что было до исправления.
        """
        session = request.attendance_session
        before = {
            "started_at": _iso(session.started_at),
            "ended_at": _iso(session.ended_at),
            "status": session.status,
        }
        if request.requested_entry_at:
            session.started_at = request.requested_entry_at
        if request.requested_exit_at:
            session.ended_at = request.requested_exit_at
        if session.ended_at:
            session.duration_seconds = int(
                (session.ended_at - session.started_at).total_seconds()
            )
        session.status = "CORRECTED"
        session.calculated_at = _now()
        session.save(
            update_fields=[
                "started_at",
                "ended_at",
                "duration_seconds",
                "status",
                "calculated_at",
                "updated_at",
            ]
        )
        return {
            "before": before,
            "after": {
                "started_at": _iso(session.started_at),
                "ended_at": _iso(session.ended_at),
                "status": session.status,
            },
        }


def _empty_totals() -> dict:
    """Итоги за период. Ноль — это ноль, а не «нет данных»."""
    return {
        "seconds": 0,
        "days_with_marks": 0,
        "working_days": 0,
        "late_days": 0,
        "late_minutes": 0,
        "open_sessions": 0,
    }


def _assignment_on(assignments: list, day: date):
    """Кадровое назначение, действовавшее в этот день.

    Список отсортирован по убыванию `valid_from`, поэтому первое
    подходящее — самое позднее из начавшихся, а именно оно и действует.
    """
    for row in assignments:
        if row.valid_from and row.valid_from > day:
            continue
        if row.valid_to and row.valid_to < day:
            continue
        return row
    return None


def _state_of(
    *,
    absence: EmployeeAbsence | None,
    has_marks: bool,
    open_session: bool,
    has_schedule: bool,
    is_working: bool,
) -> tuple[str, str | None, str | None]:
    if absence is not None:
        code = absence.absence_type.code if absence.absence_type_id else None
        name = absence.absence_type.name if absence.absence_type_id else None
        if code in SICK_CODES:
            return "SICK_LEAVE", code, name
        if code in VACATION_CODES:
            return "VACATION", code, name
        return "OTHER_ABSENCE", code, name

    if open_session:
        return "IN_OFFICE", None, None
    if has_marks:
        return "LEFT", None, None
    if not has_schedule:
        # Не «прогул»: сравнивать не с чем. Отдельное состояние, чтобы
        # ненастроенный график не превращался в обвинение человеку.
        return "NO_SCHEDULE", None, None
    if not is_working:
        return "DAY_OFF", None, None
    return "NOT_COME", None, None


def _full_name(employee: Employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _now() -> datetime:
    from django.utils import timezone as django_timezone

    return django_timezone.now()
