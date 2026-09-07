"""Допустимые значения строковых статусов.

Хранятся как VARCHAR + CHECK, а не как native PostgreSQL ENUM: добавить значение
в CHECK — это одна короткая миграция, а расширение native enum блокирует таблицу
и плохо откатывается.

Один список на весь проект: модели, миграции и документация берут значения
отсюда, поэтому они не могут разъехаться. Значения перенесены без изменений
из прежнего `src/core/database/enums.py` — это часть контракта данных,
а не деталь реализации.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q


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
# Экран в офисе, показывающий меняющийся QR.
#   PENDING — заведён, но ещё не сопряжён: на руках только одноразовый код;
#   ACTIVE  — сопряжён, у него есть свой credential;
#   REVOKED — доступ отозван, новых кодов не получает.
QR_DISPLAY_DEVICE_STATUSES = ("PENDING", "ACTIVE", "REVOKED")
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
# PENDING — бот получил одноразовую ссылку и подтвердил Telegram-аккаунт,
# но HR привязку ещё не утвердил. До утверждения доступа к данным нет:
# ссылку мог открыть не тот, кому её передавали.
TELEGRAM_ACCOUNT_STATUSES = ("PENDING", "ACTIVE", "REVOKED", "BLOCKED")

# Жизненный цикл одноразовой ссылки привязки.
#   ACTIVE               — выдана HR, ещё не использована;
#   PENDING_CONFIRMATION — сотрудник перешёл по ней, ждём решения HR;
#   USED                 — HR подтвердил привязку, ссылка отработала;
#   REJECTED             — HR отклонил привязку;
#   REVOKED              — HR отозвал ссылку до того, как ею воспользовались;
#   EXPIRED              — срок вышел раньше, чем ссылкой воспользовались.
#
# Срок держится на `expires_at`, а не на статусе: строка переводится
# в EXPIRED в тот момент, когда система на неё натыкается. Так истечение
# не зависит от фонового процесса, которого может не быть.
TELEGRAM_INVITATION_STATUSES = (
    "ACTIVE", "PENDING_CONFIRMATION", "USED", "REJECTED", "REVOKED", "EXPIRED",
)
# Статусы, при которых приглашение ещё «живое» и занимает место у сотрудника.
TELEGRAM_INVITATION_OPEN_STATUSES = ("ACTIVE", "PENDING_CONFIRMATION")

# --- база знаний AI-ассистента ---
KNOWLEDGE_SOURCE_TYPES = ("FAQ", "POLICY", "INSTRUCTION", "DOCUMENT")
# INDEXING — версия готовится: чанки и эмбеддинги ещё считаются.
# ERROR — индексация не удалась; предыдущая ACTIVE-версия при этом
# продолжает отвечать сотрудникам.
KNOWLEDGE_SOURCE_STATUSES = ("DRAFT", "INDEXING", "ACTIVE", "ARCHIVED", "ERROR")
FAQ_ENTRY_STATUSES = ("DRAFT", "ACTIVE", "ARCHIVED")
UNANSWERED_QUESTION_STATUSES = ("NEW", "IN_REVIEW", "ANSWERED", "IGNORED")
LLM_QUERY_STATUSES = (
    "EXACT_FAQ", "RAG_ANSWERED", "ESCALATED", "PERSONAL_DATA", "ERROR",
)
ANSWER_FEEDBACK_RATINGS = ("HELPFUL", "NOT_HELPFUL")
INDEX_JOB_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED")

# Очередь фоновых выгрузок. Отличается от очереди индексации на два
# состояния: CANCELLED — задание отменили до запуска, а SUCCEEDED здесь
# означает, что файл существует и его можно скачать.
EXPORT_JOB_STATUSES = (
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED",
)

# Уровень действия правила. Чем выше число, тем выше приоритет:
# правило офиса перекрывает региональное, региональное — глобальное.
SCOPE_LEVEL_GLOBAL = 1
SCOPE_LEVEL_REGION = 2
SCOPE_LEVEL_OFFICE = 3
QUESTION_STATUSES = (
    "NEW", "AI_ANSWERED", "ESCALATED_TO_HR", "HR_ANSWERED", "CLOSED",
)
NOTIFICATION_CHANNELS = ("TELEGRAM", "EMAIL", "PUSH", "IN_APP")
# Очередь отправки (transactional outbox). PENDING — это и есть «в очереди»:
# заводить отдельный QUEUED значило бы иметь два имени одного состояния.
# RUNNING держит строку, которую уже взял отправщик, — без него повторный
# запуск воркера отправил бы сообщение дважды.
NOTIFICATION_STATUSES = (
    "PENDING", "RUNNING", "SENT", "FAILED", "CANCELLED", "READ",
)
# Чем кончилась ОДНА попытка отправки. Не то же самое, что статус строки:
# статус — это где уведомление сейчас, попытка — что случилось однажды.
# Строка с двумя неудачами и последующим успехом имеет статус SENT и три
# записи в истории.
NOTIFICATION_ATTEMPT_OUTCOMES = ("SENT", "FAILED", "CANCELLED")

# --- роли, создаваемые сидом ---
SYSTEM_ROLE_CODES = (
    "SUPER_ADMIN", "HR_ADMIN", "REGIONAL_HR", "OFFICE_ADMIN",
    "MANAGER", "ACCOUNTANT", "TECH_ADMIN",
)

def status_check(field: str, values: tuple[str, ...], name: str) -> models.CheckConstraint:
    """`CHECK (field IN (...))` с точным именем ограничения.

    Имя задаётся явно, а не генерируется Django: на него ссылается перевод
    ошибок целостности в понятные сообщения, и оно уже есть в базе.
    """
    return models.CheckConstraint(condition=Q(**{f"{field}__in": list(values)}), name=name)


def choices(values: tuple[str, ...]) -> list[tuple[str, str]]:
    """Тот же список значений в виде choices — для админки и форм."""
    return [(value, value) for value in values]
