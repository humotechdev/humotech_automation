"""Настоящий сервис личных HR-данных: детерминированный SQL, без LLM.

Отвечает на вопросы сотрудника о нём самом — приходы, отработанное время,
отсутствия, больничные, отпуска, остаток дней. Запросы построены руками
и параметризованы; SQL, сгенерированный моделью, здесь невозможен
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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.modules.absences.models import AbsenceType, EmployeeAbsence, LeaveBalance
from src.modules.ai_assistant.services.personal_data import (
    PersonalDataAnswer,
    PersonalDataQueryRouter,
    PersonalDataQueryService,
    PersonalIntent,
)
from src.modules.ai_assistant.services.scoping import resolve_employee_scope
from src.modules.employees.models import Employee
from src.modules.offices.models import Office
from src.modules.qr_attendance.models import AttendanceSession
from src.modules.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)

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

    def __init__(self, session: Session, *, router: PersonalDataQueryRouter | None = None):
        self.session = session
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

        employee = self.session.get(Employee, employee_id)
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

    # ------------------------------------------------------------ вспомогательное

    def _office_timezone(self, employee: Employee) -> ZoneInfo:
        """Пояс офиса сотрудника; при его отсутствии — пояс организации."""
        scope = resolve_employee_scope(self.session, employee_id=employee.id)
        name = None
        if scope is not None and scope.office_id is not None:
            name = self.session.scalar(
                select(Office.timezone).where(Office.id == scope.office_id)
            )
        if not name:
            from src.modules.organizations.models import Organization

            name = self.session.scalar(
                select(Organization.default_timezone).where(
                    Organization.id == employee.organization_id
                )
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

    def _sessions_in(self, employee: Employee, period: _Period):
        """Сессии, начавшиеся в периоде. Всегда с фильтром по организации."""
        return list(
            self.session.scalars(
                select(AttendanceSession)
                .where(
                    AttendanceSession.employee_id == employee.id,
                    AttendanceSession.organization_id == employee.organization_id,
                    AttendanceSession.started_at >= period.utc_from,
                    AttendanceSession.started_at < period.utc_to,
                    AttendanceSession.status != "INVALID",
                )
                .order_by(AttendanceSession.started_at)
            )
        )

    def _open_session(self, employee: Employee) -> AttendanceSession | None:
        return self.session.scalar(
            select(AttendanceSession).where(
                AttendanceSession.employee_id == employee.id,
                AttendanceSession.organization_id == employee.organization_id,
                AttendanceSession.status == "OPEN",
            )
        )

    def _worked_minutes(
        self, sessions, *, now: datetime
    ) -> tuple[int, bool]:
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
            self.session.scalars(
                select(EmployeeAbsence)
                .join(AbsenceType, AbsenceType.id == EmployeeAbsence.absence_type_id)
                .where(
                    EmployeeAbsence.employee_id == employee.id,
                    EmployeeAbsence.organization_id == employee.organization_id,
                    AbsenceType.code == code,
                    EmployeeAbsence.status != "CANCELLED",
                )
                .order_by(EmployeeAbsence.start_at.desc())
                .limit(20)
            )
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
                True, "Сейчас открытой сессии нет — по данным системы вы не в офисе.",
                {"in_office": False},
            )
        started = open_session.started_at.astimezone(tz)
        minutes = int((now - open_session.started_at).total_seconds() // 60)
        office = self.session.scalar(
            select(Office.name).where(Office.id == open_session.office_id)
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
        suffix = " (сессия ещё открыта, время считается на текущий момент)" if has_open else ""
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
            self.session.execute(
                select(
                    EmployeeAbsence.start_at,
                    EmployeeAbsence.end_at,
                    EmployeeAbsence.status,
                    AbsenceType.name,
                )
                .join(AbsenceType, AbsenceType.id == EmployeeAbsence.absence_type_id)
                .where(
                    EmployeeAbsence.employee_id == employee.id,
                    EmployeeAbsence.organization_id == employee.organization_id,
                    EmployeeAbsence.status != "CANCELLED",
                    EmployeeAbsence.start_at < period.utc_to,
                    EmployeeAbsence.end_at >= period.utc_from,
                )
                .order_by(EmployeeAbsence.start_at)
            ).all()
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
                f"{local_to.strftime('%d.%m')} ({_ABSENCE_STATUS_TEXT.get(status, status)})"
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
            return self._Result(
                False, f"Подтверждённого {noun} в системе нет.", {}
            )
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
            f"Период: {local_from.strftime('%d.%m.%Y')}–{local_to.strftime('%d.%m.%Y')}.",
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
        row = self.session.execute(
            select(
                LeaveBalance.allocated_minutes,
                LeaveBalance.reserved_minutes,
                LeaveBalance.used_minutes,
                LeaveBalance.adjustment_minutes,
            )
            .join(AbsenceType, AbsenceType.id == LeaveBalance.absence_type_id)
            .where(
                LeaveBalance.employee_id == employee.id,
                LeaveBalance.organization_id == employee.organization_id,
                LeaveBalance.year == year,
                AbsenceType.code == ANNUAL_LEAVE_CODE,
            )
        ).first()

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
        row = self.session.execute(
            select(WorkSchedule.id, WorkSchedule.weekly_minutes)
            .join(
                EmployeeScheduleAssignment,
                EmployeeScheduleAssignment.schedule_id == WorkSchedule.id,
            )
            .where(
                EmployeeScheduleAssignment.employee_id == employee.id,
                EmployeeScheduleAssignment.organization_id == employee.organization_id,
                EmployeeScheduleAssignment.valid_from <= today,
                or_(
                    EmployeeScheduleAssignment.valid_to.is_(None),
                    EmployeeScheduleAssignment.valid_to >= today,
                ),
            )
            .order_by(EmployeeScheduleAssignment.valid_from.desc())
            .limit(1)
        ).first()
        if row is None or not row.weekly_minutes:
            return None

        working_days = self.session.scalar(
            select(func.count())
            .select_from(ScheduleDay)
            .where(
                ScheduleDay.schedule_id == row.id,
                ScheduleDay.is_working_day.is_(True),
            )
        )
        if not working_days:
            return None
        return int(row.weekly_minutes // working_days)
