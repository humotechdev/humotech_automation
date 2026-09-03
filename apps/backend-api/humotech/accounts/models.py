"""Учётная запись для входа в CRM: HR, администраторы, руководители, техадмины.

Отдельной таблицы `admins` нет и не должно быть: администратор — это
users + roles + user_role_scopes.

Модель наследует `AbstractBaseUser`, но НЕ `PermissionsMixin`. Причина в схеме:
`PermissionsMixin` добавил бы в `users` колонку `is_superuser` и две таблицы
связей с группами и правами Django — то есть вторую систему прав рядом с уже
существующей `roles` + `user_role_scopes`. Двух систем прав в проекте быть
не должно, поэтому `has_perm` и признак staff выводятся из своих ролей.
"""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower

from humotech.core.enums import USER_STATUSES, choices, status_check
from humotech.core.models import (
    ArchivableModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)

# Роли, дающие доступ в Django Admin. Админка — закрытый технический
# интерфейс, а не основная CRM, поэтому список намеренно короткий.
ADMIN_ROLE_CODES = ("SUPER_ADMIN", "TECH_ADMIN")


class UserManager(BaseUserManager):
    """Создание учётных записей. Пароль всегда хешируется, открытым не хранится."""

    use_in_migrations = False

    def create_user(self, email: str, password: str | None = None, **extra):
        if not email:
            raise ValueError("Электронная почта обязательна")
        if "organization" not in extra and "organization_id" not in extra:
            raise ValueError(
                "Учётная запись обязана принадлежать организации: "
                "организация — корень изоляции данных"
            )
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra):
        """Полные права выдаются РОЛЬЮ, а не колонкой в `users`.

        Отдельного признака суперпользователя в схеме нет: доступ определяют
        роль `SUPER_ADMIN` и её область в `user_role_scopes`. Поэтому одной
        этой команды недостаточно — область назначается отдельно.
        """
        extra.setdefault("status", "ACTIVE")
        return self.create_user(email, password, **extra)

    def get_by_natural_key(self, username):
        return self.get(email__iexact=username)


class User(UUIDPrimaryKeyModel, TimestampedModel, ArchivableModel, AbstractBaseUser):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        db_column="organization_id",
        db_index=False,
        related_name="users",
    )
    email = models.CharField(max_length=255)
    # Только хеш (argon2/bcrypt). Открытый пароль не хранится нигде и никогда.
    # Колонка называется password_hash — имя говорит, что внутри, и не даёт
    # принять её за место для пароля.
    password = models.TextField(db_column="password_hash")
    status = models.CharField(max_length=30, choices=choices(USER_STATUSES))
    mfa_enabled = models.BooleanField(db_default=False)
    failed_login_attempts = models.IntegerField(db_default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login = models.DateTimeField(db_column="last_login_at", null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "users"
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"
        constraints = [
            status_check("status", USER_STATUSES, "ck_users_status"),
            # Почта уникальна ВНУТРИ организации и без учёта регистра.
            # Глобальной уникальности нет намеренно: один и тот же адрес
            # может принадлежать людям в разных организациях.
            models.UniqueConstraint(
                models.F("organization"), Lower("email"),
                name="uq_users_org_lower_email",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_users_organization_id"),
        ]

    def __str__(self) -> str:
        return self.email

    # --- доступ в админку выводится из ролей, а не из колонки ---

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE" and self.archived_at is None

    @property
    def is_staff(self) -> bool:
        """Django Admin — закрытый технический интерфейс.

        Пускаем только носителей `SUPER_ADMIN` или `TECH_ADMIN`: остальные
        работают через основную CRM, где действуют обычные проверки прав.
        """
        return self._has_admin_role()

    @property
    def is_superuser(self) -> bool:
        return self._role_codes().intersection({"SUPER_ADMIN"}) != set()

    def _role_codes(self) -> set[str]:
        from humotech.accounts.selectors import active_role_codes

        return active_role_codes(self)

    def _has_admin_role(self) -> bool:
        return bool(self._role_codes().intersection(ADMIN_ROLE_CODES))

    def has_perm(self, perm: str, obj=None) -> bool:
        """Право проверяется по своей RBAC, а не по `auth_permission`.

        Django Admin вызывает этот метод; параллельной системы прав
        в проекте нет, поэтому ответ даёт та же таблица `role_permissions`,
        что и остальной backend.
        """
        from humotech.accounts.selectors import permission_codes

        if not self.is_active:
            return False
        if self.is_superuser:
            return True
        return perm in permission_codes(self)

    def has_module_perms(self, app_label: str) -> bool:
        return self.is_active and self._has_admin_role()
