"""Как отчёты статистики выглядят в ответе API.

Отдельный модуль, а не методы на датаклассах: `statistics.py` считает и не
должен знать про JSON, а виду ответа полезно быть в одном месте — бот
и Mini App разбирают одни и те же поля, и разъехаться им негде.

Секунды отдаются секундами. Ни «8ч 30м», ни «8.5»: округление и склонение —
дело клиента, а сервер, округлив однажды, теряет разницу навсегда.
"""

from __future__ import annotations

from humotech.attendance.statistics import CurrentStatus, DayRecord, PeriodReport


def session_json(session) -> dict:
    return {
        "id": session.id,
        "day": session.day.isoformat(),
        "started_at": session.started_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "seconds": session.seconds,
        "is_open": session.is_open,
        # Прямой признак того, что число ещё вырастет. Без него клиент
        # показал бы время открытой сессии наравне с закрытой, и человек
        # решил бы, что рабочий день уже посчитан.
        "is_preliminary": session.is_preliminary,
        "office_name": session.office_name,
        "entry_point_name": session.entry_point_name,
        "exit_point_name": session.exit_point_name,
    }


def day_json(day: DayRecord) -> dict:
    return {
        "day": day.day.isoformat(),
        "seconds": day.seconds,
        "sessions_count": len(day.sessions),
        "has_open_session": day.has_open_session,
        "is_working_day": day.is_working_day,
        "attended": day.attended,
        "missed": day.missed,
        "absence_code": day.absence_code,
        "absence_name": day.absence_name,
    }


def summary_json(report: PeriodReport) -> dict:
    return {
        "first": report.first.isoformat(),
        "last": report.last.isoformat(),
        "timezone": report.timezone,
        "seconds": report.seconds,
        "completed_sessions": report.completed_sessions,
        "open_sessions": report.open_sessions,
        # null, а не ноль: график не назначен — значит, рабочих дней
        # не «ноль», а «неизвестно». Ноль читался бы как безупречная
        # посещаемость.
        "working_days": report.working_days,
        "attended_days": report.attended_days,
        "missed_days": report.missed_days,
        "sick_leave_days": report.sick_leave_days,
        "vacation_days": report.vacation_days,
        "other_absence_days": report.other_absence_days,
        "has_schedule": report.has_schedule,
    }


def status_json(status: CurrentStatus) -> dict:
    return {
        "state": status.state,
        "day": status.day.isoformat(),
        "timezone": status.timezone,
        # Открытая сессия и «часы сегодня» — разные числа, и отдаются
        # отдельно. В три часа ночи у зашедшего в 22:00 «сегодня» честно
        # ноль, а в офисе он пять часов: сессия принадлежит вчерашнему дню.
        "seconds_today": status.seconds_today,
        "open_session": (
            session_json(status.open_session) if status.open_session else None
        ),
        "last_entry_at": (
            status.last_entry_at.isoformat() if status.last_entry_at else None
        ),
        "last_exit_at": (
            status.last_exit_at.isoformat() if status.last_exit_at else None
        ),
        "scheduled_start": (
            status.scheduled_start.isoformat() if status.scheduled_start else None
        ),
        "scheduled_end": (
            status.scheduled_end.isoformat() if status.scheduled_end else None
        ),
        "absence_name": status.absence_name,
    }


__all__ = ["day_json", "session_json", "status_json", "summary_json"]
