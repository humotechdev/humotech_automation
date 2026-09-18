"""Привязка Telegram-аккаунта к сотруднику.

Именно отсюда бот узнаёт, КТО сканирует QR: сотрудник определяется по
привязанному Telegram-аккаунту, а не по данным, присланным клиентом.

Две таблицы с разными задачами:

  * `TelegramAccount` — текущее состояние доступа. Одна строка на сотрудника,
    она же отвечает на вопрос «пускать ли». Истории здесь нет намеренно:
    привязка бывает только одна, а прошлое пишется в приглашениях и аудите;
  * `TelegramLinkInvitation` — одноразовая ссылка и вся история попыток
    привязки: кто выдал, кто перешёл, что решил HR.

Привязка не становится рабочей сама по себе. Ссылку мог открыть не тот,
кому её передавали, поэтому первый результат перехода — `PENDING`, а доступ
появляется только после подтверждения HR.
"""

from __future__ import annotations

from django.db import models

from humotech.core.enums import (
    TELEGRAM_ACCOUNT_STATUSES,
    TELEGRAM_INVITATION_OPEN_STATUSES,
    TELEGRAM_INVITATION_STATUSES,
    choices,
    status_check,
)
from humotech.core.models import (
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class TelegramAccount(UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel):
    """Текущая привязка. Одна строка на сотрудника — навсегда.

    Перепривязка не создаёт новую строку, а меняет эту: у доступа нет истории,
    у него есть только текущее состояние. Кто и когда менял — в `audit_logs`,
    через какую ссылку — в `telegram_link_invitations`.

    Отсюда же смысл двух уникальных ключей: сотрудник не может иметь двух
    Telegram, а Telegram не может принадлежать двум сотрудникам. Второй ключ
    безусловный: отозванная привязка продолжает занимать идентификатор, и
    попытка привязать его другому человеку падает с понятным конфликтом,
    а не проходит молча.
    """

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="telegram_account",
    )
    telegram_user_id = models.BigIntegerField()
    telegram_chat_id = models.BigIntegerField()
    telegram_username = models.CharField(max_length=255, null=True, blank=True)
    language_code = models.CharField(max_length=10, db_default="ru")
    status = models.CharField(
        max_length=20, choices=choices(TELEGRAM_ACCOUNT_STATUSES)
    )
    connected_at = models.DateTimeField()
    last_interaction_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "telegram_accounts"
        verbose_name = "привязка Telegram"
        verbose_name_plural = "привязки Telegram"
        constraints = [
            # Один сотрудник — один Telegram, один Telegram — один сотрудник.
            models.UniqueConstraint(
                fields=["employee"], name="uq_telegram_accounts_employee"
            ),
            models.UniqueConstraint(
                fields=["telegram_user_id"], name="uq_telegram_accounts_tg_user"
            ),
            status_check(
                "status", TELEGRAM_ACCOUNT_STATUSES, "ck_telegram_accounts_status"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_telegram_accounts_organization_id"
            ),
        ]

    def __str__(self) -> str:
        return self.telegram_username or str(self.telegram_user_id)


class TelegramLinkInvitation(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Одноразовая ссылка, которой сотрудник привязывает свой Telegram.

    Открытого токена в базе нет — только SHA-256 от него. Утечка дампа не даёт
    ни одной рабочей ссылки: восстановить 256 бит случайности из хеша нельзя,
    а перебирать нечего. Именно поэтому и сравнение идёт по хешу: сервер сам
    не знает токена, который выдал.

    Приглашение — запись о попытке привязки целиком, от выдачи ссылки до
    решения HR. Текущая привязка живёт в `TelegramAccount` — там одна строка
    на сотрудника, и именно она отвечает на вопрос «есть ли доступ». Разделение
    намеренное: у попыток есть история, у доступа — только текущее состояние.

    `consumed_by_telegram_user_id` — идентификатор, пришедший от Telegram через
    бота. Он записывается ДО решения HR, чтобы в момент подтверждения было
    видно, кто именно перешёл по ссылке.
    """

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="telegram_invitations",
    )
    # SHA-256 в шестнадцатеричном виде — ровно 64 символа.
    # Медленный хеш (argon2) здесь не нужен и был бы вреден: токен не пароль,
    # у него 256 бит случайности, перебор невозможен, а проверка выполняется
    # на каждом переходе по ссылке.
    token_hash = models.CharField(max_length=64)
    #: Имя в Telegram, которое кадровик указал в карточке при приёме.
    #:
    #: Это ПОДСКАЗКА для узнавания, а не удостоверение личности. Имя
    #: меняется и передаётся другому человеку, поэтому само по себе оно
    #: доступа не даёт: узнанный по нему приходит к тому же окну
    #: подтверждения, что и перешедший по ссылке, и решает всё равно
    #: кадровик.
    #:
    #: Живёт на приглашении, а не на карточке сотрудника: подсказка
    #: временная и истекает вместе с ним.
    expected_username = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(
        max_length=30, choices=choices(TELEGRAM_INVITATION_STATUSES)
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    consumed_by_telegram_user_id = models.BigIntegerField(null=True, blank=True)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        db_column="created_by_user_id",
        db_index=False,
        related_name="+",
    )
    reviewed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="reviewed_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "telegram_link_invitations"
        verbose_name = "приглашение к привязке Telegram"
        verbose_name_plural = "приглашения к привязке Telegram"
        constraints = [
            # Поиск по токену идёт по этому ключу: он же и уникальность,
            # и индекс. Один и тот же токен не может существовать дважды.
            models.UniqueConstraint(
                fields=["token_hash"], name="uq_telegram_link_invitations_token"
            ),
            status_check(
                "status", TELEGRAM_INVITATION_STATUSES,
                "ck_telegram_link_invitations_status",
            ),
            # У сотрудника не больше одной живой ссылки. Иначе HR, нажав
            # «создать» дважды, рассылает две рабочие ссылки и уже не знает,
            # какая из них у человека на руках.
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(
                    status__in=TELEGRAM_INVITATION_OPEN_STATUSES
                ),
                name="uq_telegram_link_invitations_open_employee",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"],
                name="ix_telegram_link_invitations_org_id",
            ),
            models.Index(
                fields=["employee"],
                name="ix_telegram_link_invitations_employee_id",
            ),
            # Списки HR всегда спрашивают «что сейчас ждёт решения»,
            # то есть фильтруют по статусу и читают с конца.
            models.Index(
                fields=["organization", "status", "-created_at"],
                name="ix_telegram_link_invitations_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} / {self.status}"

    @property
    def is_open(self) -> bool:
        return self.status in TELEGRAM_INVITATION_OPEN_STATUSES
