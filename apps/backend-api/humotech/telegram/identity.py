"""Кто этот сотрудник и открыт ли ему доступ — одна проверка на все входы.

Входов в сотрудническую часть два: Mini App с внутренним токеном и бот,
который ходит в backend от имени человека. Проверка при этом обязана быть
одна. Две копии условий разошлись бы на первой же правке: кто-то добавил бы
проверку увольнения в Mini App и забыл в боте, и уволенный продолжал бы
отмечаться из чата.

Условия допуска, все сразу:

  * привязка Telegram существует и она ACTIVE. PENDING — это «HR ещё не
    подтвердил», а не «почти можно»;
  * сотрудник не уволен и не в архиве;
  * организация активна. Замороженная организация не должна отдавать данные
    ни одному из своих сотрудников;
  * есть действующее назначение — офис, в котором человек сейчас числится.
    Без него неизвестно, к какому офису относятся его отметки и в каком
    поясе считать его сутки, поэтому доступ закрыт, а не «открыт наполовину».

Проверка перечитывается на КАЖДОМ запросе. Ни срок токена, ни кеш здесь не
участвуют: HR отключает привязку и доступ пропадает со следующего действия,
а не когда-нибудь потом.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from humotech.core.timeframes import office_zone, zone
from humotech.employees.models import EmployeeAssignment
from humotech.telegram.models import TelegramAccount

# Статусы сотрудника, при которых доступа нет.
BLOCKED_EMPLOYMENT_STATUSES = ("TERMINATED", "ARCHIVED")

# Почему отказано. Наружу эти строки уходят в усечённом виде (см. ниже),
# внутрь — целиком, чтобы журнал и тесты называли причину точно.
DENIED_NOT_LINKED = "not_linked"
DENIED_PENDING = "pending_confirmation"
DENIED_REVOKED = "link_revoked"
DENIED_BLOCKED = "link_blocked"
DENIED_EMPLOYEE_INACTIVE = "employee_inactive"
DENIED_ORGANIZATION_INACTIVE = "organization_inactive"
DENIED_NO_ASSIGNMENT = "no_assignment"


@dataclass(frozen=True)
class EmployeeContext:
    """Всё, что нужно знать о сотруднике, чтобы обслужить его запрос.

    Собирается один раз на запросе и передаётся дальше. Сервисы не берут
    ни `employee_id`, ни `organization_id` из тела запроса — только отсюда,
    поэтому подделать принадлежность к организации негде.
    """

    account: TelegramAccount
    employee: object
    assignment: EmployeeAssignment
    organization: object

    @property
    def office(self):
        return self.assignment.office

    @property
    def organization_id(self):
        return self.organization.id

    @property
    def timezone(self) -> ZoneInfo:
        """Пояс, в котором для этого человека считаются сутки."""
        return office_zone(self.office)


@dataclass(frozen=True)
class AccessDenied:
    """Отказ с внутренней причиной.

    Причин семь, а наружу их уходит две. Это не небрежность: `reason`
    и `public_reason` отвечают разным собеседникам.

    `public_reason` — для Mini App, то есть для кого угодно, кто открыл
    страницу. Там различаются ровно два состояния: «HR ещё не подтвердил»
    и «доступа нет». Отозванная привязка намеренно неотличима от
    отсутствующей — иначе ответ сервера превращается в справочник «этот
    Telegram когда-то принадлежал сотруднику».

    `reason` — для бота. Бот предъявляет общий секрет, то есть он наша же
    сторона, а не произвольный клиент; ему нужна точная причина, чтобы
    выбрать формулировку человеку. Пересказывать её дальше он не обязан.
    """

    reason: str

    @property
    def public_reason(self) -> str:
        if self.reason == DENIED_PENDING:
            return DENIED_PENDING
        return DENIED_NOT_LINKED


def resolve_by_telegram_user_id(
    telegram_user_id: int, *, now: datetime | None = None
) -> EmployeeContext | AccessDenied:
    """Найти сотрудника по подтверждённому Telegram ID.

    `telegram_user_id` обязан приходить из настоящего Telegram Update —
    проверку этого делает вызывающий (общий секрет бота). Здесь считается,
    что число уже подтверждено.
    """
    account = (
        TelegramAccount.objects.select_related("employee", "organization")
        .filter(telegram_user_id=telegram_user_id)
        .first()
    )
    if account is None:
        return AccessDenied(DENIED_NOT_LINKED)
    return resolve_account(account, now=now)


def resolve_account(
    account: TelegramAccount, *, now: datetime | None = None
) -> EmployeeContext | AccessDenied:
    """Полная проверка допуска по уже найденной привязке."""
    if account.status == "PENDING":
        return AccessDenied(DENIED_PENDING)
    if account.status == "REVOKED":
        return AccessDenied(DENIED_REVOKED)
    if account.status == "BLOCKED":
        return AccessDenied(DENIED_BLOCKED)
    if account.status != "ACTIVE":
        # Неизвестный статус — закрыто. Новый статус, который забыли
        # разобрать здесь, не должен по умолчанию давать доступ.
        return AccessDenied(DENIED_NOT_LINKED)

    employee = account.employee
    if (
        employee.employment_status in BLOCKED_EMPLOYMENT_STATUSES
        or employee.archived_at is not None
    ):
        return AccessDenied(DENIED_EMPLOYEE_INACTIVE)

    organization = account.organization
    if organization.status != "ACTIVE":
        return AccessDenied(DENIED_ORGANIZATION_INACTIVE)

    assignment = current_assignment(employee.id, organization=organization, now=now)
    if assignment is None:
        return AccessDenied(DENIED_NO_ASSIGNMENT)

    return EmployeeContext(
        account=account,
        employee=employee,
        assignment=assignment,
        organization=organization,
    )


def current_assignment(
    employee_id, *, organization=None, now: datetime | None = None
) -> EmployeeAssignment | None:
    """Действующее назначение сотрудника на сегодня.

    «Сегодня» берётся в поясе организации, а не сервера: назначение —
    величина в датах, и в Новосибирске новый день наступает на четыре часа
    раньше московского сервера. Пояс офиса тут ещё неизвестен: офис как раз
    и находится через назначение.

    Основное назначение (`is_primary`) идёт первым: у совместителя может
    быть несколько действующих, и офисом по умолчанию считается основной.
    """
    moment = now or timezone.now()
    tz = zone(getattr(organization, "default_timezone", None))
    at: date = moment.astimezone(tz).date()

    return (
        EmployeeAssignment.objects.select_related("office", "position", "department")
        .filter(employee_id=employee_id, valid_from__lte=at)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=at))
        .order_by("-is_primary", "-valid_from", "-created_at")
        .first()
    )


__all__ = [
    "AccessDenied",
    "BLOCKED_EMPLOYMENT_STATUSES",
    "EmployeeContext",
    "current_assignment",
    "resolve_account",
    "resolve_by_telegram_user_id",
]
