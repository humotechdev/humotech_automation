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
# Документы при приёме. «Будет сформирован» — это не файл, а обещание
# системы: договор и приказ печатаются позже, и до тех пор их состояние
# отличается и от «нет», и от «загружен».
EMPLOYEE_DOCUMENT_KINDS = ("IDENTITY", "CONTRACT", "HIRE_ORDER", "OTHER")
# Пол и семейное положение — анкетные поля кадровой карточки. Оба
# необязательны: у сотрудников, заведённых до их появления, значения нет,
# и требовать его задним числом означало бы не дать открыть их карточку.
GENDERS = ("MALE", "FEMALE")
MARITAL_STATUSES = ("SINGLE", "MARRIED", "DIVORCED", "WIDOWED")
EMPLOYEE_DOCUMENT_STATUSES = ("MISSING", "UPLOADED", "GENERATED_LATER", "REVIEW")

# --- пользователи ---
USER_STATUSES = ("ACTIVE", "INACTIVE", "LOCKED", "ARCHIVED")

# --- графики ---
SCHEDULE_STATUSES = ("ACTIVE", "INACTIVE", "ARCHIVED")
CALENDAR_EXCEPTION_TYPES = ("HOLIDAY", "SHORT_DAY", "WORKING_WEEKEND", "CLOSURE")

# --- QR ---
# --- опросы сотрудников ---
#
# Опрос именной: HR видит, кто и как ответил. Анонимного вида здесь нет
# и не появится незаметно — это другой продукт с другими обещаниями.
SURVEY_QUESTION_KINDS = ("SINGLE", "MULTI", "SCALE", "TEXT")
SURVEY_CAMPAIGN_STATUSES = ("DRAFT", "SCHEDULED", "ACTIVE", "FINISHED", "CANCELLED")
SURVEY_AUDIENCE_KINDS = ("EMPLOYEES", "DEPARTMENT", "OFFICE", "ALL")
SURVEY_RECIPIENT_STATUSES = ("PENDING", "SENT", "STARTED", "COMPLETED")

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
#: Что человек сам сказал про свой день в ответ на напоминание.
#: «Не приду» здесь — это предупреждение, а не оформленное отсутствие:
#: отпуск и больничный проходят согласование и живут своими заявками.
DAY_NOTICE_KINDS = ("LATE", "ABSENT")
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
    "CANCELLED", "DOCUMENT_ATTACHED", "DOCUMENT_VERIFIED",
    # Справку не приняли. Отдельно от `DOCUMENT_VERIFIED`: «проверен» и
    # «отклонён» — разные исходы, и записывать отказ как проверку значит
    # потерять его в истории заявки.
    "DOCUMENT_REJECTED", "COMMENTED",
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

# --- первичное ознакомление ---
#
# Где человек в программе. Статус ВЫЧИСЛЯЕМЫЙ: он хранится в строке ради
# списков и фильтров, но правду о допуске говорит не он, а пересчёт —
# см. `humotech/onboarding/progress.py`. Иначе публикация новой версии
# обязательного документа не смогла бы вернуть уже «завершившего»
# человека к подтверждению: колонка осталась бы COMPLETED.
#
#   NOT_STARTED                — приглашение выдано, бот ещё не открыт;
#   IN_PROGRESS                — читает информационные карточки;
#   INFO_COMPLETED             — все карточки прочитаны, документы не начаты;
#   POLICIES_IN_PROGRESS       — часть обязательных документов подтверждена;
#   COMPLETED                  — карточки прочитаны, все документы приняты;
#   BLOCKED_BY_DECLINED_POLICY — человек отказался подтвердить документ.
ONBOARDING_STATUSES = (
    "NOT_STARTED", "IN_PROGRESS", "INFO_COMPLETED",
    "POLICIES_IN_PROGRESS", "COMPLETED", "BLOCKED_BY_DECLINED_POLICY",
)

# Жизненный цикл РЕДАКЦИИ обязательного документа. Версия неизменяема
# после публикации: подтверждение сотрудника относится к конкретному
# тексту, и правка опубликованного означала бы, что человек согласился
# не с тем, что подписано его именем.
POLICY_VERSION_STATUSES = ("DRAFT", "PUBLISHED", "ARCHIVED")

# Что сотрудник решил по конкретной редакции. Отказ — такое же решение,
# как согласие: он записывается, а не стирается, иначе «не подтвердил»
# и «не дошёл» стали бы неразличимы.
POLICY_DECISIONS = ("ACCEPTED", "DECLINED")

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
# --- обращения сотрудников ---
# NEW — никто не взял; IN_PROGRESS — у ответственного; WAITING_EMPLOYEE —
# кадровик спросил уточнение и ждёт человека; CLOSED — вопрос снят.
QUESTION_STATUSES = ("NEW", "IN_PROGRESS", "WAITING_EMPLOYEE", "CLOSED")
QUESTION_PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")
# Тема вопроса, а не вид заявки: отпуск и больничный здесь — о чём
# спрашивают, оформляются они по-прежнему в «Заявках».
QUESTION_CATEGORIES = (
    "VACATION", "SICK_LEAVE", "ATTENDANCE", "SCHEDULE", "SALARY",
    "DOCUMENTS", "TELEGRAM", "OTHER",
)
QUESTION_MESSAGE_KINDS = ("EMPLOYEE", "HR", "SYSTEM")
QUESTION_MESSAGE_SOURCES = ("TELEGRAM", "CRM", "SYSTEM")
# Системные события ленты. Каждое изменение состояния или ответственного
# оставляет строку — лента и журнал аудита рассказывают одно и то же.
QUESTION_EVENTS = (
    "CREATED", "TAKEN", "ASSIGNED", "TRANSFERRED", "PRIORITY", "CATEGORY",
    "WAITING_EMPLOYEE", "RESUMED", "CLOSED", "REOPENED",
)
# Чем кончилась попытка ассистента подготовить черновик.
QUESTION_DRAFT_STATUSES = ("READY", "LOW_CONFIDENCE", "CONFLICT", "NO_SOURCES")
# Доставка ответа HR глазами кадровика — выводится из строки очереди.
# UNKNOWN — ответ перенесён из времён до очереди, строки у него нет.
QUESTION_DELIVERY_STATUSES = ("QUEUED", "DELIVERED", "READ", "FAILED", "UNKNOWN")
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
    "MANAGER", "ACCOUNTANT", "TECH_ADMIN", "VIEWER",
)

# Роли, которые предлагают при выдаче доступа в интерфейсе. Каталог шире:
# в нём есть служебные и переносные роли, и отдавать их выбором из списка
# незачем. Уже выданную роль вне этого набора интерфейс всё равно
# показывает — иначе у живого администратора роль выглядела бы пустой.
OFFERED_ROLE_CODES = (
    "SUPER_ADMIN", "HR_ADMIN", "OFFICE_ADMIN", "MANAGER", "VIEWER",
)

def status_check(
    field: str, values: tuple[str, ...], name: str, *, nullable: bool = False
) -> models.CheckConstraint:
    """`CHECK (field IN (...))` с точным именем ограничения.

    Имя задаётся явно, а не генерируется Django: на него ссылается перевод
    ошибок целостности в понятные сообщения, и оно уже есть в базе.

    `nullable=True` разрешает NULL. Без него необязательное поле с таким
    ограничением стало бы обязательным на уровне базы: `NULL IN (...)`
    даёт NULL, а не истину, и строка без значения не прошла бы проверку.
    """
    condition = Q(**{f"{field}__in": list(values)})
    if nullable:
        condition = Q(**{f"{field}__isnull": True}) | condition
    return models.CheckConstraint(condition=condition, name=name)


def choices(values: tuple[str, ...]) -> list[tuple[str, str]]:
    """Тот же список значений в виде choices — для админки и форм."""
    return [(value, value) for value in values]
