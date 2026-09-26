"""Главная страница CRM: сколько людей в каком состоянии прямо сейчас.

Каждая карточка — это ЧИСЛО и АДРЕС, по которому видно, из кого оно
сложилось. Число без такого адреса бесполезно: кадровик, увидев «не
пришли: 7», первым делом спрашивает «кто эти семеро», и если ответить
нечем, он открывает Excel и считает заново.

Цифры не пересчитываются заново, а берутся из того же `presence()`,
которым отдаётся список. Иначе рано или поздно карточка показала бы
одно число, а список по ней — другое, и объяснить расхождение было бы
нечем.

Незакрытые сессии и заявки на решение считаются шире одного дня
намеренно: сессия, забытая открытой в пятницу, — это проблема и в
понедельник, а заявка, поданная неделю назад, не перестаёт ждать
ответа оттого, что выбран сегодняшний день.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from django.db.models import Q

from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceSession
from humotech.core.rbac import Actor
from humotech.core.service import BaseService
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.services import (
    WORKING_STATUSES,
    current_primary_assignment_filter,
)


@dataclass(frozen=True)
class Card:
    """Одно число главной страницы.

    `endpoint` и `params` вместе дают готовый запрос за списком: клиент
    подставляет их как есть, а не собирает адрес по своим правилам.
    Собранный на клиенте адрес однажды разойдётся с тем, по которому
    посчитано число, и никто этого не заметит.
    """

    key: str
    title: str
    value: int
    endpoint: str | None = None
    params: dict = field(default_factory=dict)
    # Показатель, на который стоит посмотреть. Не «плохо», а «разберитесь»:
    # незакрытая сессия может оказаться ночной сменой, а не забывчивостью.
    attention: bool = False


@dataclass(frozen=True)
class Dashboard:
    date: date
    timezone: str
    cards: list[Card]
    warnings: list[dict]


class DashboardService(BaseService):
    """Сводка главной страницы. Требует `attendance.read`."""

    def summary(
        self,
        actor: Actor,
        *,
        day: date | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        position_id: uuid.UUID | None = None,
        schedule_id: uuid.UUID | None = None,
    ) -> Dashboard:
        self.access.require(actor, "attendance.read")

        attendance = AttendanceHrService()
        report = attendance.presence(
            actor,
            day=day,
            office_id=office_id,
            region_id=region_id,
            department_id=department_id,
            position_id=position_id,
            schedule_id=schedule_id,
        )
        counts = report.counts()

        # Общие параметры списка: те же, что пришли сюда. Клиент подставляет
        # их вместе с `state`, и список гарантированно совпадает с числом.
        common = _clean(
            {
                "date": report.day.isoformat(),
                "office_id": office_id,
                "region_id": region_id,
                "department_id": department_id,
                "position_id": position_id,
                "schedule_id": schedule_id,
            }
        )
        presence_card = lambda key, title, state, attention=False: Card(  # noqa: E731
            key=key,
            title=title,
            value=counts.get(state, 0),
            endpoint="/api/v1/attendance/presence",
            params={**common, "state": state},
            attention=attention,
        )

        came = counts.get("IN_OFFICE", 0) + counts.get("LEFT", 0)
        should_work = came + counts.get("NOT_COME", 0)
        late = sum(
            1 for row in report.rows if row.late_minutes and row.late_minutes > 0
        )

        cards = [
            Card(
                key="active_employees",
                title="Активные сотрудники",
                value=self._active_employees(
                    actor,
                    day=report.day,
                    office_id=office_id,
                    region_id=region_id,
                ),
                endpoint="/api/v1/employees",
                # Перечислением, а не одним значением: список принимает
                # статусы через запятую, и ссылка обязана открыть ровно
                # тех, кого карточка посчитала.
                params=_clean({"status": ",".join(WORKING_STATUSES),
                               "office_id": office_id,
                               "region_id": region_id}),
            ),
            Card(
                key="should_work_today",
                title="Должны работать сегодня",
                value=should_work,
                endpoint="/api/v1/attendance/presence",
                params=common,
            ),
            Card(
                key="came",
                title="Пришли",
                value=came,
                endpoint="/api/v1/attendance/presence",
                params=common,
            ),
            presence_card("in_office", "Сейчас в офисе", "IN_OFFICE"),
            presence_card("left", "Уже ушли", "LEFT"),
            Card(
                key="late",
                title="Опоздали",
                value=late,
                endpoint="/api/v1/attendance/presence",
                params=common,
                attention=late > 0,
            ),
            presence_card("not_come", "Не пришли", "NOT_COME", attention=True),
            presence_card("sick_leave", "На больничном", "SICK_LEAVE"),
            presence_card("vacation", "В отпуске", "VACATION"),
            presence_card("other_absence", "Прочее отсутствие", "OTHER_ABSENCE"),
            presence_card("day_off", "Выходной по графику", "DAY_OFF"),
            presence_card(
                "no_schedule", "Без графика", "NO_SCHEDULE",
                attention=counts.get("NO_SCHEDULE", 0) > 0,
            ),
        ]

        open_sessions = self._open_sessions(
            actor, office_id=office_id, region_id=region_id
        )
        cards.append(
            Card(
                key="open_sessions",
                title="Незакрытые сессии",
                value=open_sessions,
                endpoint="/api/v1/attendance/sessions",
                params=_clean({"open": "true", "office_id": office_id,
                               "region_id": region_id}),
                attention=open_sessions > 0,
            )
        )

        pending = self._pending_requests(actor)
        cards.append(
            Card(
                key="pending_requests",
                title="Заявки ждут решения",
                value=pending,
                endpoint="/api/v1/absence-requests/pending",
                params={},
                attention=pending > 0,
            )
        )

        return Dashboard(
            date=report.day,
            timezone=report.timezone,
            cards=cards,
            warnings=self._warnings(report),
        )

    # ------------------------------------------------------------ внутреннее

    def _active_employees(
        self,
        actor: Actor,
        *,
        day: date,
        office_id: uuid.UUID | None,
        region_id: uuid.UUID | None,
    ) -> int:
        """Сколько человек числится, а не сколько сегодня работает.

        Это разные числа: в штате сто человек, а по графику сегодня выходят
        восемьдесят. Складывать их в одну карточку нельзя.
        """
        condition = Q()
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

        employee_ids = EmployeeAssignment.objects.filter(
            current_primary_assignment_filter(day) & condition
        ).values_list("employee_id", flat=True)

        return Employee.objects.filter(
            organization_id=actor.organization_id,
            # Тот же набор, что и в составе смены: разойдись они —
            # «в офисе» оказалось бы больше, чем всего работающих.
            employment_status__in=WORKING_STATUSES,
            id__in=employee_ids,
        ).count()

    def _open_sessions(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID | None,
        region_id: uuid.UUID | None,
    ) -> int:
        queryset = AttendanceSession.objects.filter(
            organization_id=actor.organization_id, ended_at__isnull=True
        )
        if office_id:
            queryset = queryset.filter(office_id=office_id)
        elif region_id:
            queryset = queryset.filter(office__region_id=region_id)
        else:
            visible = self.access.visible_office_ids(actor)
            if visible is not None:
                queryset = queryset.filter(office_id__in=visible)
        return queryset.count()

    def _pending_requests(self, actor: Actor) -> int:
        """Заявки, ждущие решения.

        Считаются только если у человека есть право их видеть: карточка
        «12 заявок ждут» тому, кто не может открыть ни одну, — это утечка
        числа, а не полезная сводка.
        """
        if not self.access.has(actor, "absences.read"):
            return 0
        # Считает тот же `pending()`, на который ведёт карточка: с областью
        # видимости. Раньше здесь был счёт по всей организации, и кадровик
        # одного офиса видел число открытых заявок всей компании — и число
        # не совпадало со списком, открывающимся по нажатию.
        from humotech.absences.services import AbsenceService

        return AbsenceService().pending(actor).count()

    @staticmethod
    def _warnings(report) -> list[dict]:
        """То, что стоит посмотреть глазами.

        Не «нарушения»: система измеряет посещаемость, а не дисциплину.
        Отметка в день больничного может означать и оформленный задним
        числом больничный, и чужую отметку, и ошибку в датах.
        """
        warnings = []
        conflicting = [row for row in report.rows if row.conflicting_marks]
        if conflicting:
            warnings.append(
                {
                    "code": "marks_during_absence",
                    "title": "Отметки в дни оформленного отсутствия",
                    "count": len(conflicting),
                    "employee_ids": [str(row.employee_id) for row in conflicting],
                }
            )
        without_schedule = [row for row in report.rows if row.state == "NO_SCHEDULE"]
        if without_schedule:
            warnings.append(
                {
                    "code": "employees_without_schedule",
                    "title": "Сотрудники без рабочего графика",
                    "count": len(without_schedule),
                    "employee_ids": [
                        str(row.employee_id) for row in without_schedule
                    ],
                }
            )
        return warnings


def _clean(params: dict) -> dict:
    """Убрать пустые параметры и привести идентификаторы к строкам."""
    return {
        key: str(value) for key, value in params.items() if value not in (None, "")
    }
