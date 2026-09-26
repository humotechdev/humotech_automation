"""Контекст обращения: кто спрашивает и что с ним уже происходит.

Кадровик отвечает на вопрос «когда мой отпуск» не по тексту вопроса, а
по заявке на отпуск и остатку дней. Поэтому рядом с перепиской лежит
то, что о человеке известно системе.

Каждый раздел отдаётся, только если у пользователя есть право видеть
его и в остальной CRM. Раздела без права нет вовсе (`null`), а не
пустой список: «заявок нет» и «заявки вам не показаны» — разные ответы.
"""

from __future__ import annotations

from datetime import time

from django.db.models import Q
from django.utils import timezone

from humotech.core.rbac import AccessControl, Actor
from humotech.questions.inbox import (
    OPEN,
    _draft,
    _full_name,
    _places,
    _telegram,
)
from humotech.questions.models import EmployeeQuestion

RECENT = 5
MINUTES_PER_WORKING_DAY = 8 * 60
WEEKDAYS = {1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб", 7: "Вс"}
OPEN_REQUESTS = ("SUBMITTED", "IN_REVIEW")


def question_context(access: AccessControl, actor: Actor, question: EmployeeQuestion) -> dict:
    employee = question.employee

    def has(code: str) -> bool:
        return access.has(actor, code)

    place = _places([employee.id]).get(employee.id) or {}
    telegram = _telegram(employee.id)
    telegram["username"] = _username(employee.id) if has("telegram.read") else None

    return {
        "employee": {
            "id": employee.id,
            "full_name": _full_name(employee),
            "employee_number": employee.employee_number,
            "employment_status": employee.employment_status,
            "has_photo": employee.photo_id is not None,
            "position": place.get("position"),
            "department": place.get("department"),
            "office": place.get("office"),
            "schedule": _schedule(employee.id) if has("schedules.read") or has("employees.read") else None,
            "telegram": telegram,
        },
        "links": {
            "employee_card": has("employees.read"),
            "attendance": has("attendance.read"),
            "requests": has("absences.read"),
        },
        "requests": _requests(employee.id) if has("absences.read") else None,
        "balance": _balance(employee.id) if has("absences.read") else None,
        "corrections": _corrections(employee.id) if has("attendance.read") else None,
        "documents": _documents(employee.id) if has("employees.read") else None,
        "history": _history(question),
        "today": _today(access, actor, employee, place) if has("attendance.read") else None,
        "materials": _materials(question) if has("knowledge.read") else None,
    }


def _today(access, actor: Actor, employee, place: dict) -> dict:
    """Где человек сегодня — тем же расчётом, что и страница посещаемости.

    Своего правила «на работе» здесь нет: второе правило однажды
    разошлось бы с первым, и кадровик увидел бы «да» там, где посещаемость
    говорит «не пришёл». День — по поясу офиса сотрудника, а не сервера.
    """
    from humotech.attendance.hr import AttendanceHrService
    from humotech.core.timeframes import office_zone, zone
    from humotech.offices.models import Office

    office = place.get("office")
    row = Office.objects.filter(id=office["id"]).first() if office else None
    # Без офиса — пояс организации: полночь по UTC для Ташкента наступает
    # в пять утра, и «сегодня» до пяти было бы вчерашним днём.
    tz = office_zone(row) if row is not None else zone(
        getattr(employee.organization, "default_timezone", None)
    )
    day = timezone.now().astimezone(tz).date()
    report = AttendanceHrService().presence(
        actor, day=day, employee_id=employee.id,
    )
    mine = next((one for one in report.rows if one.employee_id == employee.id), None)
    if mine is None:
        return {"day": day, "state": None, "first_entry_at": None, "last_exit_at": None}
    return {
        "day": day,
        "state": mine.state,
        "first_entry_at": mine.first_entry_at,
        "last_exit_at": mine.last_exit_at,
    }


def _username(employee_id) -> str | None:
    from humotech.telegram.models import TelegramAccount

    return (
        TelegramAccount.objects.filter(employee_id=employee_id)
        .values_list("telegram_username", flat=True)
        .first()
    )


def _schedule(employee_id) -> dict | None:
    """Действующий график и его рабочие дни одной строкой: «Пн–Пт · 09:00–18:00»."""
    from humotech.schedules.models import EmployeeScheduleAssignment, ScheduleDay

    today = timezone.localdate()
    row = (
        EmployeeScheduleAssignment.objects.select_related("schedule")
        .filter(employee_id=employee_id, valid_from__lte=today)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        .order_by("-valid_from")
        .first()
    )
    if row is None:
        return None
    days = list(
        ScheduleDay.objects.filter(schedule_id=row.schedule_id, is_working_day=True)
        .order_by("weekday")
        .values("weekday", "start_time", "end_time")
    )
    return {
        "name": row.schedule.name,
        "flexible": row.schedule.is_flexible,
        "summary": _summary(days),
    }


def _summary(days: list[dict]) -> str | None:
    """Подряд идущие дни с одинаковым временем — одним диапазоном."""
    if not days:
        return None
    groups: list[list[dict]] = []
    for day in days:
        last = groups[-1][-1] if groups else None
        if (
            last is not None
            and day["weekday"] == last["weekday"] + 1
            and day["start_time"] == last["start_time"]
            and day["end_time"] == last["end_time"]
        ):
            groups[-1].append(day)
        else:
            groups.append([day])

    parts = []
    for group in groups:
        first, final = group[0], group[-1]
        label = (
            WEEKDAYS[first["weekday"]]
            if first is final
            else f"{WEEKDAYS[first['weekday']]}–{WEEKDAYS[final['weekday']]}"
        )
        hours = _hours(first["start_time"], first["end_time"])
        parts.append(f"{label} · {hours}" if hours else label)
    return "; ".join(parts)


def _hours(start: time | None, end: time | None) -> str | None:
    if start is None or end is None:
        return None
    return f"{start:%H:%M}–{end:%H:%M}"


def _requests(employee_id) -> list[dict]:
    from humotech.absences.models import AbsenceRequest

    rows = (
        AbsenceRequest.objects.filter(employee_id=employee_id)
        .exclude(status="DRAFT")
        .select_related("absence_type")
        .order_by("-created_at")[:20]
    )
    # Незакрытые — первыми: по ним вопрос скорее всего и задан.
    ordered = sorted(rows, key=lambda row: row.status not in OPEN_REQUESTS)[:RECENT]
    return [
        {
            "id": row.id,
            "type": row.absence_type.name,
            "type_code": row.absence_type.code,
            "kind": row.request_kind,
            "status": row.status,
            "start": row.requested_start_at,
            "end": row.requested_end_at,
            "created_at": row.created_at,
        }
        for row in ordered
    ]


def _balance(employee_id) -> list[dict]:
    from humotech.absences.models import LeaveBalance

    rows = (
        LeaveBalance.objects.filter(
            employee_id=employee_id,
            year=timezone.localdate().year,
            absence_type__deducts_leave_balance=True,
        )
        .select_related("absence_type")
        .order_by("absence_type__name")
    )
    return [
        {
            "type": row.absence_type.name,
            "year": row.year,
            "allocated_days": _days(row.allocated_minutes + row.adjustment_minutes),
            "used_days": _days(row.used_minutes),
            "reserved_days": _days(row.reserved_minutes),
            "available_days": _days(
                row.allocated_minutes + row.adjustment_minutes
                - row.used_minutes - row.reserved_minutes
            ),
        }
        for row in rows
    ]


def _days(minutes: int) -> float:
    return round(minutes / MINUTES_PER_WORKING_DAY, 2)


def _corrections(employee_id) -> list[dict]:
    from humotech.attendance.models import AttendanceCorrectionRequest

    rows = (
        AttendanceCorrectionRequest.objects.filter(employee_id=employee_id)
        .exclude(status="DRAFT")
        .order_by("-submitted_at")[:RECENT]
    )
    return [
        {
            "id": row.id,
            "status": row.status,
            "submitted_at": row.submitted_at,
            "requested_entry_at": row.requested_entry_at,
            "requested_exit_at": row.requested_exit_at,
            "reason": row.reason,
        }
        for row in rows
    ]


def _documents(employee_id) -> list[dict]:
    from humotech.employees.models import EmployeeDocument

    rows = EmployeeDocument.objects.filter(employee_id=employee_id).order_by(
        "-updated_at"
    )[:RECENT]
    return [
        {
            "id": row.id,
            "title": row.title,
            "kind": row.kind,
            "status": row.status,
            "has_file": row.file_id is not None,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


def _history(question: EmployeeQuestion) -> dict:
    rows = EmployeeQuestion.objects.filter(
        organization_id=question.organization_id, employee_id=question.employee_id
    )
    total = rows.count()
    closed = rows.filter(status="CLOSED").count()
    recent = rows.exclude(id=question.id).order_by("-created_at")[:RECENT]
    return {
        "total": total,
        "closed": closed,
        "open": rows.filter(status__in=OPEN).count(),
        "recent": [
            {
                "id": row.id,
                "number": row.number,
                "topic": row.normalized_topic or row.question_text[:120],
                "status": row.status,
                "created_at": row.created_at,
            }
            for row in recent
        ],
    }


def _materials(question: EmployeeQuestion) -> list[dict]:
    draft = _draft(question)
    return draft["sources"] if draft else []
