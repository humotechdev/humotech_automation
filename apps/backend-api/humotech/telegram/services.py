"""Привязка Telegram к сотруднику: выдача ссылки, переход, решение HR.

Порядок шагов — это и есть защита, поэтому он записан здесь целиком:

  1. HR выдаёт одноразовую ссылку конкретному сотруднику. В базу ложится
     только хеш токена; открытый токен возвращается ровно один раз, в ответе
     на создание, и больше не восстановим ниоткуда;
  2. сотрудник открывает ссылку, бот сообщает backend токен и подтверждённый
     Telegram ID. Привязка создаётся в состоянии PENDING — доступа она ещё
     не даёт;
  3. HR подтверждает. Только после этого привязка становится ACTIVE.

Второй шаг не даёт доступа намеренно. Ссылку можно переслать, потерять,
показать через плечо — по ней привязывается тот, кто её открыл, и HR обязан
увидеть, кто это был, до того как человек получит доступ к своим данным.

Что здесь считается конфликтом:

  * у сотрудника уже есть рабочая привязка — новую ссылку выдавать нельзя,
    сначала отключить старую. Иначе «выдал ссылку» тихо означало бы
    «сменил владельца доступа»;
  * у сотрудника уже есть живая ссылка — вторую не выдаём. Иначе HR,
    нажав дважды, рассылает две рабочие ссылки и не знает, какая у человека;
  * Telegram-аккаунт уже привязан к другому сотруднику — отказ. Один
    аккаунт не может быть двумя людьми.

Гонка при переходе по ссылке закрыта блокировкой строки приглашения
(`select_for_update`) внутри транзакции: два одновременных перехода
выстраиваются в очередь, и второй видит уже погашенный токен. Уникальные
ключи `telegram_accounts` — второй рубеж на случай, если запросы придут
не через этот сервис.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from humotech.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.selectors import require_visible_employee
from humotech.notifications import messages
from humotech.notifications.outbox import enqueue
from humotech.telegram.identity import (
    # Статусы сотрудника, при которых привязка невозможна, определены там же,
    # где и проверка допуска: два списка разошлись бы.
    BLOCKED_EMPLOYMENT_STATUSES,
    AccessDenied,
    EmployeeContext,
    resolve_account,
    resolve_by_telegram_user_id,
)
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation
from humotech.telegram.tokens import (
    build_invitation_link,
    generate_invitation_token,
    hash_invitation_token,
)

# Что попадает в журнал. Токена здесь нет и быть не может — ни открытого,
# ни хеша: `AuditTrail` вычёркивает `token_hash` дополнительно, но правильнее
# просто не собирать его.
INVITATION_AUDIT_FIELDS = (
    "employee_id", "status", "expires_at", "used_at", "revoked_at",
    "reviewed_at", "consumed_by_telegram_user_id",
)
ACCOUNT_AUDIT_FIELDS = (
    "employee_id", "status", "telegram_user_id", "telegram_username",
    "connected_at", "revoked_at",
)

ENTITY_INVITATION = "telegram_link_invitations"
ENTITY_ACCOUNT = "telegram_accounts"

# Что показать человеку при отказе. Различаются ровно три случая, потому что
# делать в них надо разное: подождать, попросить ссылку, идти к HR. Все
# остальные причины сведены к последнему — по разнице ответов иначе
# выясняют, кто заведён в системе.
MINI_APP_REFUSALS = {
    "not_linked": "Telegram не привязан к сотруднику",
    "pending_confirmation": "Привязка ожидает подтверждения отдела кадров",
    "access_denied": "Доступ закрыт: обратитесь в отдел кадров",
}


@dataclass(frozen=True)
class IssuedInvitation:
    """Ссылка, отданная HR ровно один раз.

    `token` и `link` существуют только в этом ответе: в базе лежит хеш,
    и повторно показать ссылку невозможно даже суперпользователю. Потерянная
    ссылка отзывается и выдаётся заново — это дешевле, чем хранить рабочий
    секрет в таблице.
    """

    invitation: TelegramLinkInvitation
    token: str
    link: str


@dataclass(frozen=True)
class LinkStatus:
    """Что HR видит в карточке сотрудника."""

    employee: Employee
    account: TelegramAccount | None
    invitation: TelegramLinkInvitation | None

    @property
    def state(self) -> str:
        """Одно слово для интерфейса.

        NOT_LINKED     — привязки нет и ссылка не выдавалась;
        INVITED        — ссылка выдана, сотрудник ещё не переходил;
        PENDING        — сотрудник перешёл, ждём решения HR;
        ACTIVE         — привязка работает;
        REVOKED        — привязка была и отключена.
        """
        if self.account is not None and self.account.status == "ACTIVE":
            return "ACTIVE"
        if self.account is not None and self.account.status == "PENDING":
            return "PENDING"
        if self.invitation is not None and self.invitation.status == "ACTIVE":
            return "INVITED"
        if self.account is not None:
            return "REVOKED"
        return "NOT_LINKED"


class TelegramLinkError(Conflict):
    """Переход по ссылке не удался.

    Отдельный класс, чтобы бот мог различить причины и написать человеку
    что-то осмысленное. Наружу уходит только код — ни токена, ни того,
    существует ли такой сотрудник.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message, details={"reason": reason})


class TelegramLinkService(BaseService):
    """Требует `telegram.read` для чтения и `telegram.manage` для изменений."""

    # ------------------------------------------------------------ вспомогательное

    def _now(self) -> datetime:
        return timezone.now()

    def _invitation_ttl(self) -> timedelta:
        return timedelta(seconds=settings.TELEGRAM["INVITATION_TTL_SECONDS"])

    def _expire_stale(self, employee_id: uuid.UUID) -> None:
        """Переводит просроченные живые ссылки сотрудника в EXPIRED.

        Срок держится на `expires_at`, а не на фоновом процессе, но статус
        всё-таки нужно материализовать: частичный уникальный ключ считает
        живыми ACTIVE и PENDING_CONFIRMATION, и просроченная строка иначе
        занимала бы место, не давая выдать новую ссылку.
        """
        TelegramLinkInvitation.objects.filter(
            employee_id=employee_id, status="ACTIVE", expires_at__lte=self._now()
        ).update(status="EXPIRED", updated_at=self._now())

    def _require_employee_linkable(self, employee: Employee) -> None:
        if employee.employment_status in BLOCKED_EMPLOYMENT_STATUSES:
            raise Conflict(
                "Сотрудник не работает: привязать Telegram нельзя",
                details={"employment_status": employee.employment_status},
            )
        if employee.archived_at is not None:
            raise Conflict("Сотрудник в архиве: привязать Telegram нельзя")

    def _account_of(self, employee_id: uuid.UUID) -> TelegramAccount | None:
        return TelegramAccount.objects.filter(employee_id=employee_id).first()

    def _visible_invitations(self, actor: Actor):
        """Приглашения организации актора, ограниченные его областью видимости."""
        queryset = TelegramLinkInvitation.objects.filter(
            organization_id=actor.organization_id
        )
        visible = self.access.visible_office_ids(actor)
        if visible is None:
            return queryset
        # Сотрудник виден, если хоть один его период назначения был в офисе
        # из области — то же правило, что и в кадровом сервисе.
        employee_ids = EmployeeAssignment.objects.filter(
            office_id__in=visible
        ).values_list("employee_id", flat=True)
        return queryset.filter(employee_id__in=employee_ids)

    def _require_invitation(
        self, actor: Actor, invitation_id: uuid.UUID
    ) -> TelegramLinkInvitation:
        invitation = TelegramLinkInvitation.objects.filter(
            id=invitation_id, organization_id=actor.organization_id
        ).first()
        if invitation is None:
            # чужая организация отвечает так же, как отсутствие записи
            raise NotFound("Приглашение не найдено")
        # проверка области — через сотрудника, правило там одно на весь проект
        require_visible_employee(self.access, actor, invitation.employee_id)
        return invitation

    # ------------------------------------------------------------------ чтение

    def status(self, actor: Actor, employee_id: uuid.UUID) -> LinkStatus:
        self.access.require(actor, "telegram.read")
        employee = require_visible_employee(self.access, actor, employee_id)
        self._expire_stale(employee_id)
        return LinkStatus(
            employee=employee,
            account=self._account_of(employee_id),
            invitation=(
                TelegramLinkInvitation.objects.filter(employee_id=employee_id)
                .order_by("-created_at", "-id")
                .first()
            ),
        )

    def pending(
        self, actor: Actor, *, limit: int | None = None, cursor: str | None = None
    ) -> Page:
        """Переходы, ожидающие решения HR."""
        self.access.require(actor, "telegram.read")
        queryset = self._visible_invitations(actor).filter(
            status="PENDING_CONFIRMATION"
        ).select_related("employee")
        return paginate(queryset, limit=limit, cursor=cursor)

    def list_invitations(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "telegram.read")
        queryset = self._visible_invitations(actor).select_related("employee")
        if employee_id is not None:
            require_visible_employee(self.access, actor, employee_id)
            queryset = queryset.filter(employee_id=employee_id)
        if status:
            queryset = queryset.filter(status=status)
        return paginate(queryset, limit=limit, cursor=cursor)

    # --------------------------------------------------------- выдача ссылки

    def create_invitation(
        self, actor: Actor, employee_id: uuid.UUID
    ) -> IssuedInvitation:
        self.access.require(actor, "telegram.manage")
        employee = require_visible_employee(self.access, actor, employee_id)
        self._require_employee_linkable(employee)

        bot_username = settings.TELEGRAM["BOT_USERNAME"]
        if not bot_username:
            # Без имени бота ссылка получилась бы битой, а понять это можно
            # было бы только по молчанию сотрудника.
            raise ValidationFailed(
                "Имя бота не настроено: задайте TELEGRAM_BOT_USERNAME"
            )

        token = generate_invitation_token()
        now = self._now()

        with self.atomic():
            # Просрочку материализуем в той же транзакции: иначе истёкшая
            # строка займёт место живой и выдать новую ссылку не выйдет.
            self._expire_stale(employee_id)

            account = self._account_of(employee_id)
            if account is not None and account.status in ("ACTIVE", "PENDING"):
                raise Conflict(
                    "У сотрудника уже есть привязка Telegram: "
                    "сначала отключите её",
                    details={"status": account.status},
                )
            if TelegramLinkInvitation.objects.filter(
                employee_id=employee_id, status__in=("ACTIVE", "PENDING_CONFIRMATION")
            ).exists():
                raise Conflict(
                    "У сотрудника уже есть действующая ссылка: "
                    "отзовите её, прежде чем выдавать новую"
                )

            invitation = TelegramLinkInvitation.objects.create(
                organization_id=actor.organization_id,
                employee=employee,
                token_hash=hash_invitation_token(token),
                status="ACTIVE",
                expires_at=now + self._invitation_ttl(),
                created_by_user_id=actor.user_id,
            )
            self.audit.record(
                actor,
                action="telegram.invitation.create",
                entity_type=ENTITY_INVITATION,
                entity_id=invitation.id,
                after=snapshot(invitation, INVITATION_AUDIT_FIELDS),
            )

        return IssuedInvitation(
            invitation=invitation,
            token=token,
            link=build_invitation_link(bot_username, token),
        )

    def revoke_invitation(
        self, actor: Actor, invitation_id: uuid.UUID
    ) -> TelegramLinkInvitation:
        """Отзыв ссылки, которой ещё не воспользовались."""
        self.access.require(actor, "telegram.manage")
        invitation = self._require_invitation(actor, invitation_id)

        if invitation.status != "ACTIVE":
            raise Conflict(
                f"Ссылку нельзя отозвать в статусе {invitation.status}",
                details={"status": invitation.status},
            )

        before = snapshot(invitation, INVITATION_AUDIT_FIELDS)
        invitation.status = "REVOKED"
        invitation.revoked_at = self._now()

        with self.atomic():
            invitation.save(update_fields=["status", "revoked_at", "updated_at"])
            self.audit.record(
                actor,
                action="telegram.invitation.revoke",
                entity_type=ENTITY_INVITATION,
                entity_id=invitation.id,
                before=before,
                after=snapshot(invitation, INVITATION_AUDIT_FIELDS),
            )
        return invitation

    # ------------------------------------------------------- решение HR

    def confirm(self, actor: Actor, invitation_id: uuid.UUID) -> TelegramAccount:
        """Подтверждение привязки. Только отсюда она становится рабочей."""
        self.access.require(actor, "telegram.manage")
        invitation = self._require_invitation(actor, invitation_id)

        if invitation.status != "PENDING_CONFIRMATION":
            raise Conflict(
                f"Приглашение не ждёт подтверждения (статус {invitation.status})",
                details={"status": invitation.status},
            )

        account = self._account_of(invitation.employee_id)
        if account is None or account.status != "PENDING":
            # Рассогласование: привязку успели отключить другим путём.
            raise Conflict("Привязка не ждёт подтверждения")
        if account.telegram_user_id != invitation.consumed_by_telegram_user_id:
            # Подтверждать надо ровно тот аккаунт, который перешёл по ссылке.
            raise Conflict("Привязка изменилась: подтвердите её заново")

        before_account = snapshot(account, ACCOUNT_AUDIT_FIELDS)
        before_invitation = snapshot(invitation, INVITATION_AUDIT_FIELDS)
        now = self._now()

        account.status = "ACTIVE"
        account.connected_at = now
        account.revoked_at = None
        invitation.status = "USED"
        invitation.reviewed_at = now
        invitation.reviewed_by_user_id = actor.user_id

        with self.atomic():
            account.save(
                update_fields=["status", "connected_at", "revoked_at", "updated_at"]
            )
            invitation.save(
                update_fields=[
                    "status", "reviewed_at", "reviewed_by_user", "updated_at",
                ]
            )
            self.audit.record(
                actor,
                action="telegram.link.confirm",
                entity_type=ENTITY_ACCOUNT,
                entity_id=account.id,
                before=before_account,
                after=snapshot(account, ACCOUNT_AUDIT_FIELDS),
            )
            self.audit.record(
                actor,
                action="telegram.invitation.confirm",
                entity_type=ENTITY_INVITATION,
                entity_id=invitation.id,
                before=before_invitation,
                after=snapshot(invitation, INVITATION_AUDIT_FIELDS),
            )
            # Той же транзакцией: подтверждение и сообщение о нём либо есть
            # оба, либо нет ни одного.
            enqueue(
                organization_id=account.organization_id,
                employee_id=account.employee_id,
                notification_type="telegram.link.confirmed",
                body=messages.LINK_CONFIRMED,
                idempotency_key=f"telegram-link:{invitation.id}:confirmed",
                related_entity_type=ENTITY_ACCOUNT,
                related_entity_id=account.id,
            )
        return account

    def reject(self, actor: Actor, invitation_id: uuid.UUID) -> TelegramAccount | None:
        """Отклонение: по ссылке перешёл не тот, кому её выдавали."""
        self.access.require(actor, "telegram.manage")
        invitation = self._require_invitation(actor, invitation_id)

        if invitation.status != "PENDING_CONFIRMATION":
            raise Conflict(
                f"Приглашение не ждёт решения (статус {invitation.status})",
                details={"status": invitation.status},
            )

        account = self._account_of(invitation.employee_id)
        before_invitation = snapshot(invitation, INVITATION_AUDIT_FIELDS)
        now = self._now()

        invitation.status = "REJECTED"
        invitation.reviewed_at = now
        invitation.reviewed_by_user_id = actor.user_id

        with self.atomic():
            invitation.save(
                update_fields=[
                    "status", "reviewed_at", "reviewed_by_user", "updated_at",
                ]
            )
            if account is not None and account.status == "PENDING":
                before_account = snapshot(account, ACCOUNT_AUDIT_FIELDS)
                account.status = "REVOKED"
                account.revoked_at = now
                account.save(update_fields=["status", "revoked_at", "updated_at"])
                self.audit.record(
                    actor,
                    action="telegram.link.reject",
                    entity_type=ENTITY_ACCOUNT,
                    entity_id=account.id,
                    before=before_account,
                    after=snapshot(account, ACCOUNT_AUDIT_FIELDS),
                )
            self.audit.record(
                actor,
                action="telegram.invitation.reject",
                entity_type=ENTITY_INVITATION,
                entity_id=invitation.id,
                before=before_invitation,
                after=snapshot(invitation, INVITATION_AUDIT_FIELDS),
            )
            if account is not None:
                # Отправляется, хотя привязка уже отозвана. Это тот самый
                # человек, который открывал ссылку, — сообщить ему об отказе
                # и нужно, и безопасно: чат берётся из ЕГО же строки
                # привязки, а не откуда-то ещё. Исключение объявлено
                # в humotech/notifications/outbox.py по типу уведомления.
                enqueue(
                    organization_id=account.organization_id,
                    employee_id=account.employee_id,
                    notification_type="telegram.link.rejected",
                    body=messages.LINK_REJECTED,
                    idempotency_key=f"telegram-link:{invitation.id}:rejected",
                    related_entity_type=ENTITY_ACCOUNT,
                    related_entity_id=account.id,
                )
        return account

    def disconnect(self, actor: Actor, employee_id: uuid.UUID) -> TelegramAccount:
        """Отключение привязки. После этого ни бот, ни Mini App не пускают."""
        self.access.require(actor, "telegram.manage")
        require_visible_employee(self.access, actor, employee_id)

        account = self._account_of(employee_id)
        if account is None:
            raise NotFound("У сотрудника нет привязки Telegram")
        if account.status not in ("ACTIVE", "PENDING"):
            raise Conflict(
                f"Привязка уже не действует (статус {account.status})",
                details={"status": account.status},
            )

        before = snapshot(account, ACCOUNT_AUDIT_FIELDS)
        now = self._now()
        account.status = "REVOKED"
        account.revoked_at = now

        with self.atomic():
            account.save(update_fields=["status", "revoked_at", "updated_at"])
            # Живая ссылка вместе с привязкой теряет смысл: если оставить её,
            # человек перейдёт по ней и снова окажется в PENDING.
            open_invitations = list(
                TelegramLinkInvitation.objects.filter(
                    employee_id=employee_id,
                    status__in=("ACTIVE", "PENDING_CONFIRMATION"),
                )
            )
            for invitation in open_invitations:
                before_invitation = snapshot(invitation, INVITATION_AUDIT_FIELDS)
                invitation.status = "REVOKED"
                invitation.revoked_at = now
                invitation.reviewed_at = now
                invitation.reviewed_by_user_id = actor.user_id
                invitation.save(
                    update_fields=[
                        "status", "revoked_at", "reviewed_at", "reviewed_by_user",
                        "updated_at",
                    ]
                )
                self.audit.record(
                    actor,
                    action="telegram.invitation.revoke",
                    entity_type=ENTITY_INVITATION,
                    entity_id=invitation.id,
                    before=before_invitation,
                    after=snapshot(invitation, INVITATION_AUDIT_FIELDS),
                )
            self.audit.record(
                actor,
                action="telegram.link.disconnect",
                entity_type=ENTITY_ACCOUNT,
                entity_id=account.id,
                before=before,
                after=snapshot(account, ACCOUNT_AUDIT_FIELDS),
            )
        return account

    # ------------------------------------------------- переход по ссылке (бот)

    def consume(
        self,
        *,
        token: str,
        telegram_user_id: int,
        telegram_chat_id: int,
        telegram_username: str | None = None,
        language_code: str | None = None,
    ) -> TelegramAccount:
        """Погашение ссылки. Вызывается ботом, не пользователем CRM.

        Прав здесь не спрашивают: право даёт сам токен. Поэтому всё, что
        решает, — его состояние, и проверяется оно под блокировкой строки.

        Результат — привязка в состоянии PENDING. Доступа она не даёт.
        """
        token_hash = hash_invitation_token(token)
        now = self._now()

        with transaction.atomic():
            invitation = (
                TelegramLinkInvitation.objects.select_for_update()
                .filter(token_hash=token_hash)
                .first()
            )
            if invitation is None:
                # Несуществующий и подделанный токен неразличимы намеренно.
                raise TelegramLinkError("invalid", "Ссылка недействительна")

            if invitation.status == "PENDING_CONFIRMATION":
                raise TelegramLinkError(
                    "pending", "Ссылка уже использована, привязка ждёт подтверждения"
                )
            if invitation.status in ("USED", "REJECTED"):
                raise TelegramLinkError("used", "Ссылка уже использована")
            if invitation.status == "REVOKED":
                raise TelegramLinkError("revoked", "Ссылка отозвана")
            if invitation.status == "EXPIRED":
                raise TelegramLinkError("expired", "Срок действия ссылки истёк")
            if invitation.expires_at <= now:
                invitation.status = "EXPIRED"
                invitation.save(update_fields=["status", "updated_at"])
                raise TelegramLinkError("expired", "Срок действия ссылки истёк")

            employee = Employee.objects.filter(id=invitation.employee_id).first()
            if employee is None or employee.employment_status in (
                BLOCKED_EMPLOYMENT_STATUSES
            ) or employee.archived_at is not None:
                raise TelegramLinkError(
                    "employee_inactive", "Привязка недоступна: обратитесь в отдел кадров"
                )

            # Один Telegram — один сотрудник. Уникальный ключ в базе скажет
            # то же самое, но сообщением «нарушено ограничение целостности»;
            # здесь причина называется своими словами.
            taken = (
                TelegramAccount.objects.filter(telegram_user_id=telegram_user_id)
                .exclude(employee_id=employee.id)
                .first()
            )
            if taken is not None:
                raise TelegramLinkError(
                    "telegram_taken",
                    "Этот Telegram уже привязан к другому сотруднику",
                )

            account = self._account_of(employee.id)
            before = snapshot(account, ACCOUNT_AUDIT_FIELDS) if account else None
            if account is None:
                account = TelegramAccount(
                    organization_id=invitation.organization_id, employee=employee
                )
            account.telegram_user_id = telegram_user_id
            account.telegram_chat_id = telegram_chat_id
            account.telegram_username = telegram_username
            account.language_code = language_code or "ru"
            account.status = "PENDING"
            account.connected_at = now
            account.revoked_at = None
            account.save()

            invitation.status = "PENDING_CONFIRMATION"
            invitation.used_at = now
            invitation.consumed_by_telegram_user_id = telegram_user_id
            invitation.save(
                update_fields=[
                    "status", "used_at", "consumed_by_telegram_user_id", "updated_at",
                ]
            )

            # Действие совершил сотрудник, а не пользователь CRM: в журнале
            # это видно по actor_employee_id.
            self.audit.record_by_employee(
                organization_id=invitation.organization_id,
                employee_id=employee.id,
                action="telegram.link.consume",
                entity_type=ENTITY_ACCOUNT,
                entity_id=account.id,
                before=before,
                after=snapshot(account, ACCOUNT_AUDIT_FIELDS),
            )
        return account


@dataclass(frozen=True)
class MiniAppSession:
    """Кто вошёл и чем ему теперь подтверждать, что это он."""

    account: TelegramAccount
    employee: Employee
    token: str
    expires_in: int


class TelegramMiniAppService(BaseService):
    """Вход в Mini App: подписанная строка Telegram -> внутренний токен.

    Ни одного значения от клиента, кроме самой `initData`, здесь не читается.
    Идентификатор Telegram берётся из проверенной подписи; сотрудник и
    организация находятся по нему в базе. Присланные клиентом `employee_id`
    или `organization_id` игнорировались бы, даже если бы приходили.
    """

    def authenticate(self, init_data: str, *, now: datetime | None = None):
        from humotech.telegram.initdata import InitDataError, verify_init_data
        from humotech.telegram.tokens import MiniAppClaims, issue_mini_app_token

        config = settings.TELEGRAM
        try:
            verified = verify_init_data(
                init_data,
                bot_token=config["BOT_TOKEN"],
                max_age_seconds=config["INIT_DATA_MAX_AGE_SECONDS"],
                now=now,
            )
        except InitDataError as exc:
            # Наружу — одна причина на все случаи. Подробность помогла бы
            # подбирать состав полей и срок, а честному клиенту она не нужна:
            # ему в любом случае надо открыть Mini App заново.
            raise PermissionDenied(
                "Не удалось подтвердить, что запрос пришёл из Telegram",
                details={"reason": "init_data_rejected"},
            ) from exc

        # Дальше — тот же шлюз, что и у бота. Отдельная цепочка проверок
        # здесь разошлась бы с ботовой на первой же правке.
        resolved = resolve_by_telegram_user_id(verified.user.id, now=now)
        if isinstance(resolved, AccessDenied):
            raise PermissionDenied(
                MINI_APP_REFUSALS.get(
                    resolved.public_reason, "Доступ закрыт: обратитесь в отдел кадров"
                ),
                details={"reason": resolved.public_reason},
            )
        account = resolved.account
        employee = resolved.employee

        account.last_interaction_at = now or timezone.now()
        account.save(update_fields=["last_interaction_at", "updated_at"])

        token = issue_mini_app_token(
            MiniAppClaims(
                telegram_account_id=str(account.id),
                telegram_user_id=account.telegram_user_id,
                employee_id=str(employee.id),
                organization_id=str(account.organization_id),
            )
        )
        return MiniAppSession(
            account=account,
            employee=employee,
            token=token,
            expires_in=config["MINI_APP_SESSION_SECONDS"],
        )

    def resolve(self, token: str) -> EmployeeContext | None:
        """Проверка внутреннего токена на каждом запросе Mini App.

        Подписи мало. Строка привязки у сотрудника одна и переиспользуется
        при перепривязке, поэтому её идентификатор не удостоверяет владельца:
        без сверки `telegram_user_id` токен прежнего аккаунта продолжал бы
        работать после того, как HR отключил привязку и выдал её другому.

        Состояние привязки тоже перечитывается: отзыв обязан действовать
        немедленно, а не с истечением срока токена.
        """
        from humotech.telegram.tokens import read_mini_app_token

        claims = read_mini_app_token(
            token,
            max_age_seconds=settings.TELEGRAM["MINI_APP_SESSION_SECONDS"],
        )
        if claims is None:
            return None

        account = (
            TelegramAccount.objects.select_related("employee", "organization")
            .filter(id=claims.telegram_account_id)
            .first()
        )
        if account is None or account.telegram_user_id != claims.telegram_user_id:
            return None

        resolved = resolve_account(account)
        return None if isinstance(resolved, AccessDenied) else resolved


__all__ = [
    "IssuedInvitation",
    "LinkStatus",
    "MiniAppSession",
    "TelegramLinkError",
    "TelegramLinkService",
    "TelegramMiniAppService",
]
