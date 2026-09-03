"""Настоящий сервис личных HR-данных: детерминированные запросы, без LLM.

Отвечает на вопросы сотрудника о нём самом — приходы, отработанное время,
отсутствия, больничные, отпуска, остаток дней. Запросы построены руками
и параметризованы ORM; SQL, сгенерированный моделью, здесь невозможен
по конструкции.

Правила, зашитые в каждый запрос:
  * фильтр по `employee_id` вызывающего И по `organization_id` — сотрудник
    физически не может получить чужие данные;
  * ничего не выдумывается: нет данных — так и говорим;
  * **незакрытая сессия не превращается в выдуманное время ухода**: считаем
    «по состоянию на сейчас» и явно это помечаем;
  * сутки, недели и месяцы отсчитываются в часовом поясе ОФИСА, а не сервера:
    иначе у ночной смены и у филиалов в другом поясе всё поедет.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Q

from humotech.absences.models import EmployeeAbsence, LeaveBalance
from humotech.ai_assistant.services.personal_data import (
    PersonalDataAnswer,
    PersonalDataQueryRouter,
    PersonalDataQueryService,
    PersonalIntent,
)
from humotech.ai_assistant.services.scoping import resolve_employee_scope
from humotech.attendance.models import AttendanceSession
from humotech.employees.models import Employee
from humotech.offices.models import Office
from humotech.schedules.models import EmployeeScheduleAssignment, ScheduleDay

SICK_LEAVE_CODE = "SICK_LEAVE"
ANNUAL_LEAVE_CODE = "ANNUAL_LEAVE"

# Сессии, которые считаются фактически отработанным временем.
COUNTED_SESSION_STATUSES = ("CLOSED", "CORRECTED")

_ABSENCE_STATUS_TEXT = {
    "PLANNED": "запланирован",
    "ACTIVE": "идёт сейчас",
    "COMPLETED": "завершён",
    "CANCELLED": "отменён",
}


@dataclass(frozen=True)
class _Period:
    """Границы периода: локальные даты для показа, UTC — для запросов."""

    label: str
    local_from: date
    local_to: date
    utc_from: datetime
    utc_to: datetime


def _fmt_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(max(0, total_minutes), 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


class SqlPersonalDataQueryService(PersonalDataQueryService):
    """Реализация поверх существующих таблиц проекта. Новых таблиц не заводит."""

    def __init__(self, *, router: PersonalDataQueryRouter | None = None):
        self.router = router or PersonalDataQueryRouter()

    def is_available(self) -> bool:
        return True

    # ------------------------------------------------------------------ вход

    def answer(
        self,
        *,
        employee_id: uuid.UUID,
        question: str,
        language: str,
        now: datetime | None = None,
    ) -> PersonalDataAnswer:
        now = now or datetime.now(tz=timezone.utc)

        employee = Employee.objects.filter(id=employee_id).first()
        if employee is None:
            return PersonalDataAnswer(
                available=False, text="Не удалось определить сотрудника."
            )

        decision = self.router.classify(question)
        if decision.intent is None:
            return PersonalDataAnswer(
                available=False,
                text=(
                    "Это вопрос о ваших личных данных, но я не понял, что именно "
                    "показать. Уточните: приход, уход, часы за неделю или месяц, "
                    "отсутствия, больничный, отпуск или остаток дней."
                ),
            )

        tz = self._office_timezone(employee)
        handlers = {
            PersonalIntent.ARRIVAL_TODAY: self._arrival_today,
            PersonalIntent.DEPARTURE_TODAY: self._departure_today,
            PersonalIntent.IN_OFFICE_NOW: self._in_office_now,
            PersonalIntent.DURATION_TODAY: self._duration_today,
            PersonalIntent.HOURS_WEEK: self._hours_week,
            PersonalIntent.HOURS_MONTH: self._hours_month,
            PersonalIntent.ABSENCE_DAYS: self._absence_days,
            PersonalIntent.SICK_LEAVE_STATUS: self._sick_status,
            PersonalIntent.SICK_LEAVE_DATES: self._sick_dates,
            PersonalIntent.VACATION_STATUS: self._vacation_status,
            PersonalIntent.VACATION_DATES: self._vacation_dates,
            PersonalIntent.LEAVE_BALANCE: self._leave_balance,
        }
        result = handlers[decision.intent](employee, tz, now)
        return PersonalDataAnswer(
            available=result.available,
            text=result.text,
            intent=decision.intent,
            data=result.data,
        )

    # ------------------------------------------------------ вспомогательное

    def _office_timezone(self, employee: Employee) -> ZoneInfo:
        """Пояс офиса сотрудника; при его отсутствии — пояс организации."""
        scope = resolve_employee_scope(employee_id=employee.id)
        name = None
        if scope is not None and scope.office_id is not None:
            name = (
                Office.objects.filter(id=scope.office_id)
                .values_list("timezone", flat=True)
                .first()
            )
        if not name:
            from humotech.organizations.models import Organization

            name = (
                Organization.objects.filter(id=employee.organization_id)
                .values_list("default_timezone", flat=True)
                .first()
            )
        try:
            return ZoneInfo(name or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    @staticmethod
    def _period(tz: ZoneInfo, first: date, last: date, label: str) -> _Period:
        start = datetime.combine(first, time.min, tzinfo=tz)
        end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz)
        return _Period(
            label=label,
            local_from=first,
            local_to=last,
            utc_from=start.astimezone(timezone.utc),
            utc_to=end.astimezone(timezone.utc),
        )

    def _sessions_in(self, employee: Employee, period: _Period) -> list:
        """Сессии, начавшиеся в периоде. Всегда с фильтром по организации."""
        return list(
            AttendanceSession.objects.filter(
                employee_id=employee.id,
                organization_id=employee.organization_id,
                started_at__gte=period.utc_from,
                started_at__lt=period.utc_to,
            )
            .exclude(status="INVALID")
            .order_by("started_at")
        )

    def _open_session(self, employee: Employee) -> AttendanceSession | None:
        return AttendanceSession.objects.filter(
            employee_id=employee.id,
            organization_id=employee.organization_id,
            status="OPEN",
        ).first()

    def _worked_minutes(self, sessions, *, now: datetime) -> tuple[int, bool]:
        """Минуты за период и признак «есть незакрытая сессия».

        У открытой сессии времени ухода НЕТ. Мы не подставляем его, а считаем
        до текущего момента и обязаны сказать об этом в ответе.
        """
        total = 0
        has_open = False
        for session in sessions:
            if session.status == "OPEN":
                has_open = True
                total += int((now - session.started_at).total_seconds() // 60)
            elif session.duration_seconds is not None:
                total += session.duration_seconds // 60
            elif session.ended_at is not None:
                total += int(
                    (session.ended_at - session.started_at).total_seconds() // 60
                )
        return total, has_open

    def _absence_of_type(
        self, employee: Employee, code: str, *, now: datetime
    ) -> EmployeeAbsence | None:
        """Текущее или ближайшее подтверждённое отсутствие нужного типа."""
        rows = list(
            EmployeeAbsence.objects.filter(
                employee_id=employee.id,
                organization_id=employee.organization_id,
                absence_type__code=code,
            )
            .exclude(status="CANCELLED")
            .order_by("-start_at")[:20]
        )
        if not rows:
            return None
        # сначала то, что идёт прямо сейчас
        for row in rows:
            if row.start_at <= now <= row.end_at:
                return row
        # затем ближайшее будущее
        future = [row for row in rows if row.start_at > now]
        if future:
            return min(future, key=lambda r: r.start_at)
        return rows[0]

    # --------------------------------------------------------------- обработчики

    @dataclass(frozen=True)
    class _Result:
        available: bool
        text: str
        data: dict

    def _arrival_today(self, employee, tz, now) -> "_Result":
        today = now.astimezone(tz).date()
        period = self._period(tz, today, today, "сегодня")
        sessions = self._sessions_in(employee, period)
        if not sessions:
            return self._Result(
                False, "Сегодня отметок о приходе нет.", {"date": today.isoformat()}
            )
        first = sessions[0].started_at.astimezone(tz)
        return self._Result(
            True,
            f"Сегодня вы отметились на входе в {first.strftime('%H:%M')}.",
            {"date": today.isoformat(), "arrival_local": first.isoformat()},
        )

    def _departure_today(self, employee, tz, now) -> "_Result":
        today = now.astimezone(tz).date()
        period = self._period(tz, today, today, "сегодня")
        sessions = self._sessions_in(employee, period)
        if not sessions:
            return self._Result(False, "Сегодня отметок нет.", {})

        closed = [s for s in sessions if s.ended_at is not None]
        open_session = next((s for s in sessions if s.status == "OPEN"), None)
        if open_session is not None:
            # выдумывать время ухода нельзя — его просто нет
            started = open_session.started_at.astimezone(tz)
            return self._Result(
                False,
                "Ухода сегодня вы ещё не отмечали: сессия открыта "
                f"с {started.strftime('%H:%M')}.",
                {"open_since_local": started.isoformat()},
            )
        last = max(closed, key=lambda s: s.ended_at).ended_at.astimezone(tz)
        return self._Result(
            True,
            f"Последняя отметка об уходе сегодня — в {last.strftime('%H:%M')}.",
            {"departure_local": last.isoformat()},
        )

    def _in_office_now(self, employee, tz, now) -> "_Result":
        open_session = self._open_session(employee)
        if open_session is None:
            return self._Result(
                True,
                "Сейчас открытой сессии нет — по данным системы вы не в офисе.",
                {"in_office": False},
            )
        started = open_session.started_at.astimezone(tz)
        minutes = int((now - open_session.started_at).total_seconds() // 60)
        office = (
            Office.objects.filter(id=open_session.office_id)
            .values_list("name", flat=True)
            .first()
        )
        return self._Result(
            True,
            f"Да, вы в офисе «{office}» с {started.strftime('%H:%M')} "
            f"— это уже {_fmt_minutes(minutes)}.",
            {
                "in_office": True,
                "since_local": started.isoformat(),
                "minutes": minutes,
                "office": office,
            },
        )

    def _duration_today(self, employee, tz, now) -> "_Result":
        today = now.astimezone(tz).date()
        period = self._period(tz, today, today, "сегодня")
        sessions = self._sessions_in(employee, period)
        if not sessions:
            return self._Result(False, "Сегодня отметок нет.", {})

        minutes, has_open = self._worked_minutes(sessions, now=now)
        suffix = (
            " (сессия ещё открыта, время считается на текущий момент)"
            if has_open else ""
        )
        return self._Result(
            True,
            f"Сегодня в офисе — {_fmt_minutes(minutes)}{suffix}.",
            {"minutes": minutes, "session_open": has_open,
             "date": today.isoformat()},
        )

    def _hours_for(self, employee, tz, now, period: _Period) -> "_Result":
        sessions = self._sessions_in(employee, period)
        minutes, has_open = self._worked_minutes(sessions, now=now)
        if not sessions:
            return self._Result(
                False,
                f"За {period.label} отметок нет.",
                {"minutes": 0, "days": 0,
                 "from": period.local_from.isoformat(),
                 "to": period.local_to.isoformat()},
            )
        days = len({s.started_at.astimezone(tz).date() for s in sessions})
        suffix = " (одна сессия ещё открыта)" if has_open else ""
        return self._Result(
            True,
            f"За {period.label} — {_fmt_minutes(minutes)} за {days} дн.{suffix}",
            {
                "minutes": minutes,
                "days": days,
                "session_open": has_open,
                "from": period.local_from.isoformat(),
                "to": period.local_to.isoformat(),
            },
        )

    def _hours_week(self, employee, tz, now) -> "_Result":
        today = now.astimezone(tz).date()
        monday = today - timedelta(days=today.weekday())
        return self._hours_for(
            employee, tz, now, self._period(tz, monday, today, "эту неделю")
        )

    def _hours_month(self, employee, tz, now) -> "_Result":
        today = now.astimezone(tz).date()
        first = today.replace(day=1)
        return self._hours_for(
            employee, tz, now, self._period(tz, first, today, "этот месяц")
        )

    def _absence_days(self, employee, tz, now) -> "_Result":
        today = now.astimezone(tz).date()
        first = today.replace(day=1)
        period = self._period(tz, first, today, "этот месяц")

        rows = list(
            EmployeeAbsence.objects.filter(
                employee_id=employee.id,
                organization_id=employee.organization_id,
                start_at__lt=period.utc_to,
                end_at__gte=period.utc_from,
            )
            .exclude(status="CANCELLED")
            .order_by("start_at")
            .values_list("start_at", "end_at", "status", "absence_type__name")
        )
        if not rows:
            return self._Result(
                True, "В этом месяце подтверждённых отсутствий нет.", {"items": []}
            )

        items = []
        lines = []
        for start_at, end_at, status, type_name in rows:
            local_from = start_at.astimezone(tz).date()
            local_to = end_at.astimezone(tz).date()
            lines.append(
                f"• {type_name}: {local_from.strftime('%d.%m')}–"
                f"{local_to.strftime('%d.%m')} "
                f"({_ABSENCE_STATUS_TEXT.get(status, status)})"
            )
            items.append(
                {"type": type_name, "from": local_from.isoformat(),
                 "to": local_to.isoformat(), "status": status}
            )
        return self._Result(
            True, "Отсутствия в этом месяце:\n" + "\n".join(lines), {"items": items}
        )

    def _absence_answer(
        self, employee, tz, now, *, code: str, noun: str, dates_only: bool
    ) -> "_Result":
        absence = self._absence_of_type(employee, code, now=now)
        if absence is None:
            return self._Result(False, f"Подтверждённого {noun} в системе нет.", {})
        local_from = absence.start_at.astimezone(tz).date()
        local_to = absence.end_at.astimezone(tz).date()
        status_text = _ABSENCE_STATUS_TEXT.get(absence.status, absence.status)
        data = {
            "from": local_from.isoformat(),
            "to": local_to.isoformat(),
            "status": absence.status,
        }
        if dates_only:
            return self._Result(
                True,
                f"Даты {noun}: с {local_from.strftime('%d.%m.%Y')} "
                f"по {local_to.strftime('%d.%m.%Y')}.",
                data,
            )
        return self._Result(
            True,
            f"Статус {noun}: {status_text}. "
            f"Период: {local_from.strftime('%d.%m.%Y')}–"
            f"{local_to.strftime('%d.%m.%Y')}.",
            data,
        )

    def _sick_status(self, employee, tz, now):
        return self._absence_answer(
            employee, tz, now, code=SICK_LEAVE_CODE, noun="больничного",
            dates_only=False,
        )

    def _sick_dates(self, employee, tz, now):
        return self._absence_answer(
            employee, tz, now, code=SICK_LEAVE_CODE, noun="больничного",
            dates_only=True,
        )

    def _vacation_status(self, employee, tz, now):
        return self._absence_answer(
            employee, tz, now, code=ANNUAL_LEAVE_CODE, noun="отпуска",
            dates_only=False,
        )

    def _vacation_dates(self, employee, tz, now):
        return self._absence_answer(
            employee, tz, now, code=ANNUAL_LEAVE_CODE, noun="отпуска",
            dates_only=True,
        )

    def _leave_balance(self, employee, tz, now) -> "_Result":
        year = now.astimezone(tz).year
        row = (
            LeaveBalance.objects.filter(
                employee_id=employee.id,
                organization_id=employee.organization_id,
                year=year,
                absence_type__code=ANNUAL_LEAVE_CODE,
            )
            .values_list(
                "allocated_minutes", "reserved_minutes", "used_minutes",
                "adjustment_minutes",
            )
            .first()
        )

        if row is None:
            return self._Result(
                False,
                f"Баланс отпуска за {year} год в системе не заведён.",
                {"year": year},
            )

        allocated, reserved, used, adjustment = row
        available = allocated + adjustment - used - reserved
        data = {
            "year": year,
            "available_minutes": available,
            "allocated_minutes": allocated,
            "reserved_minutes": reserved,
            "used_minutes": used,
            "adjustment_minutes": adjustment,
        }

        day_minutes = self._daily_norm_minutes(employee, now)
        if day_minutes:
            days = round(available / day_minutes, 1)
            data["available_days"] = days
            data["day_minutes"] = day_minutes
            text = (
                f"Остаток отпуска на {year} год: {days} дн. "
                f"({_fmt_minutes(max(0, available))}). "
                f"Зарезервировано заявками: {_fmt_minutes(reserved)}."
            )
        else:
            # Пересчитать минуты в дни без графика нельзя — и мы этого
            # не делаем, вместо того чтобы взять «обычные 8 часов» с потолка.
            text = (
                f"Остаток отпуска на {year} год: {_fmt_minutes(max(0, available))}. "
                "Пересчитать в дни не могу: у вас не назначен рабочий график."
            )
        return self._Result(True, text, data)

    def _daily_norm_minutes(self, employee: Employee, now: datetime) -> int | None:
        """Длительность рабочего дня из действующего графика сотрудника.

        Ни числа рабочих дней, ни длины смены не берём «по умолчанию»:
        и то и другое читается из графика. Нет графика — нет пересчёта в дни.
        """
        today = now.date()
        row = (
            EmployeeScheduleAssignment.objects.filter(
                Q(valid_to__isnull=True) | Q(valid_to__gte=today),
                employee_id=employee.id,
                organization_id=employee.organization_id,
                valid_from__lte=today,
            )
            .order_by("-valid_from")
            .values_list("schedule_id", "schedule__weekly_minutes")
            .first()
        )
        if row is None or not row[1]:
            return None
        schedule_id, weekly_minutes = row

        working_days = ScheduleDay.objects.filter(
            schedule_id=schedule_id, is_working_day=True
        ).count()
        if not working_days:
            return None
        return int(weekly_minutes // working_days)
