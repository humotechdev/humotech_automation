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

from humotech.core.constraints import raw_check
from humotech.core.enums import USER_STATUSES, choices, status_check
from humotech.core.functions import TransactionNow
from humotech.core.models import (
    ArchivableModel,
    CreatedAtModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)

# Роль, дающая доступ в Django Admin. Ровно одна, и это осознанно.
#
# Админка спрашивает права кодами вида `<приложение>.<действие>_<модель>`
# (`offices.change_office`), а доменный каталог оперирует другими
# (`offices.manage`). Сопоставлять одно с другим — значит завести вторую
# систему прав рядом с существующей, а её в проекте быть не должно.
# Поэтому админка остаётся аварийным входом для суперпользователя,
# а вся обычная работа идёт через CRM, где проверки настоящие.
ADMIN_ROLE_CODES = ("SUPER_ADMIN",)


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
        роль `SUPER_ADMIN` и её область в `user_role_scopes`. Поэтому учётная
        запись создаётся вместе с областью на всю организацию — иначе
        получился бы пользователь, который не может ни войти в админку,
        ни выполнить ни одной операции, и понять почему было бы нечем.
        """
        from humotech.rbac.models import Role

        extra.setdefault("status", "ACTIVE")
        role = Role.objects.filter(code="SUPER_ADMIN", organization__isnull=True).first()
        if role is None:
            raise ValueError(
                "Роль SUPER_ADMIN не найдена. Сначала наполните справочники: "
                "python manage.py seed"
            )

        user = self.create_user(email, password, **extra)
        # region_id и office_id пусты — это и означает «вся организация»
        UserRoleScope.objects.create(
            organization_id=user.organization_id, user=user, role=role
        )
        return user

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
    # NULL — техническая учётка без сотрудника (интеграция, суперадмин вендора)
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="account",
    )
    #: Логин: адрес почты или короткое имя вроде `malika.hr`. Колонка
    #: называется `email` исторически и поле не проверяется на формат
    #: адреса: организация сама решает, чем людям удобнее входить.
    email = models.CharField(max_length=255)
    #: Как человека зовут. Пусто у технической учётной записи: у неё имени
    #: нет вовсе, и подставлять туда адрес значит выдавать одно за другое.
    #: У записи, привязанной к сотруднику, имя берут из карточки — здесь
    #: оно нужно тем, у кого карточки нет.
    full_name = models.CharField(max_length=255, null=True, blank=True)
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
            # Один сотрудник — не более одной учётной записи.
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(employee__isnull=False),
                name="uq_users_employee_id",
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
        """Django Admin — аварийный вход, а не рабочий интерфейс.

        Пускаем только `SUPER_ADMIN`. Остальные работают через CRM: там
        права проверяются доменным каталогом, а не кодами моделей Django.
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

        Django Admin вызывает этот метод своими кодами
        (`offices.change_office`); их в доменном каталоге нет, поэтому
        для админки решает признак суперпользователя, а коды каталога
        отвечают на вопросы остального backend.
        """
        from humotech.accounts.selectors import permission_codes

        if not self.is_active:
            return False
        if self.is_superuser:
            return True
        return perm in permission_codes(self)

    def has_module_perms(self, app_label: str) -> bool:
        return self.is_active and self._has_admin_role()


class UserRoleScope(UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel):
    """Какая роль и на какой территории действует у пользователя.

        region_id IS NULL и office_id IS NULL  -> вся организация
        указан region_id                        -> весь регион
        указан office_id                        -> только этот офис
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        db_column="user_id",
        db_index=False,
        related_name="role_scopes",
    )
    role = models.ForeignKey(
        "rbac.Role",
        on_delete=models.PROTECT,
        db_column="role_id",
        db_index=False,
        related_name="user_scopes",
    )
    region = models.ForeignKey(
        "regions.Region",
        on_delete=models.PROTECT,
        db_column="region_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    valid_from = models.DateTimeField(db_default=TransactionNow())
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "user_role_scopes"
        verbose_name = "область роли пользователя"
        verbose_name_plural = "области ролей пользователей"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role", "region", "office", "valid_from"],
                name="uq_user_role_scopes_grant",
            ),
            # Область задаётся ЛИБО регионом, ЛИБО офисом, но не обоими сразу.
            raw_check(
                "region_id IS NULL OR office_id IS NULL",
                "ck_user_role_scopes_scope_not_both",
            ),
            raw_check(
                "valid_to IS NULL OR valid_to >= valid_from",
                "ck_user_role_scopes_valid_period",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_user_role_scopes_organization_id"
            ),
            models.Index(fields=["user"], name="ix_user_role_scopes_user_id"),
            models.Index(fields=["role"], name="ix_user_role_scopes_role_id"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} / {self.role_id}"
