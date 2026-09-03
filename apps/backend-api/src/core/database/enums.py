"""Допустимые значения строковых статусов.

Хранятся как VARCHAR + CHECK, а не как native PostgreSQL ENUM: добавить значение
в CHECK — это одна короткая миграция, а расширение native enum блокирует таблицу
и плохо откатывается.

Один список на весь проект: Python-код, миграции и документация берут значения
отсюда, поэтому они не могут разъехаться.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint

# --- организация ---
ORGANIZATION_STATUSES = ("ACTIVE", "INACTIVE", "ARCHIVED")
REGION_STATUSES = ("ACTIVE", "INACTIVE", "ARCHIVED")
OFFICE_STATUSES = ("ACTIVE", "INACTIVE", "CLOSED", "ARCHIVED")
DEPARTMENT_STATUSES = ("ACTIVE", "INACTIVE", "ARCHIVED")
POSITION_STATUSES = ("ACTIVE", "INACTIVE", "ARCHIVED")

# --- сотрудники ---
EMPLOYMENT_STATUSES = ("ACTIVE", "PROBATION", "SUSPENDED", "TERMINATED", "ARCHIVED")
EMPLOYMENT_TYPES = ("FULL_TIME", "PART_TIME", "CONTRACT", "INTERN")
WORK_MODES = ("ONSITE", "HYBRID", "REMOTE")
OFFICE_ACCESS_TYPES = ("PRIMARY", "TEMPORARY", "PERMANENT", "VISITOR")

# --- пользователи ---
USER_STATUSES = ("ACTIVE", "INACTIVE", "LOCKED", "ARCHIVED")

# --- графики ---
SCHEDULE_STATUSES = ("ACTIVE", "INACTIVE", "ARCHIVED")
CALENDAR_EXCEPTION_TYPES = ("HOLIDAY", "SHORT_DAY", "WORKING_WEEKEND", "CLOSURE")

# --- QR ---
QR_DIRECTION_MODES = ("ENTRY", "EXIT", "BOTH")
QR_MODES = ("STATIC", "ROTATING")
QR_DISPLAY_SESSION_STATUSES = ("ACTIVE", "EXPIRED", "REVOKED", "CLOSED")
DEVICE_STATUSES = ("PENDING", "TRUSTED", "REVOKED")

# --- отметки ---
ATTENDANCE_EVENT_TYPES = ("ENTRY", "EXIT")
ATTENDANCE_SOURCES = ("QR", "MANUAL", "IMPORT")
VERIFICATION_STATUSES = ("ACCEPTED", "REJECTED", "REVIEW")
ATTENDANCE_SESSION_STATUSES = ("OPEN", "CLOSED", "CORRECTED", "INVALID")
CORRECTION_REQUEST_STATUSES = (
    "DRAFT", "SUBMITTED", "IN_REVIEW", "APPROVED", "REJECTED", "CANCELLED",
)

# --- отсутствия ---
ABSENCE_REQUEST_KINDS = ("CREATE", "EXTEND", "CANCEL")
ABSENCE_REQUEST_STATUSES = (
    "DRAFT", "SUBMITTED", "IN_REVIEW", "APPROVED", "REJECTED", "CANCELLED",
)
EMPLOYEE_ABSENCE_STATUSES = ("PLANNED", "ACTIVE", "COMPLETED", "CANCELLED")
DOCUMENT_VERIFICATION_STATUSES = ("PENDING", "VERIFIED", "REJECTED")
FILE_SCAN_STATUSES = ("PENDING", "CLEAN", "INFECTED", "FAILED")
ABSENCE_ACTIONS = (
    "CREATED", "SUBMITTED", "TAKEN_IN_REVIEW", "APPROVED", "REJECTED",
    "CANCELLED", "DOCUMENT_ATTACHED", "DOCUMENT_VERIFIED", "COMMENTED",
)

# --- Telegram, знания, вопросы, уведомления ---
TELEGRAM_ACCOUNT_STATUSES = ("ACTIVE", "REVOKED", "BLOCKED")
ARTICLE_STATUSES = ("DRAFT", "IN_REVIEW", "PUBLISHED", "ARCHIVED")
QUESTION_STATUSES = (
    "NEW", "AI_ANSWERED", "ESCALATED_TO_HR", "HR_ANSWERED", "CLOSED",
)
NOTIFICATION_CHANNELS = ("TELEGRAM", "EMAIL", "PUSH", "IN_APP")
NOTIFICATION_STATUSES = ("PENDING", "SENT", "FAILED", "CANCELLED", "READ")

# --- роли, создаваемые сидом ---
SYSTEM_ROLE_CODES = (
    "SUPER_ADMIN", "HR_ADMIN", "REGIONAL_HR", "OFFICE_ADMIN",
    "MANAGER", "ACCOUNTANT", "TECH_ADMIN",
)


def in_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    """CHECK (column IN (...)) с предсказуемым именем для миграций."""
    joined = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({joined})", name=name)
