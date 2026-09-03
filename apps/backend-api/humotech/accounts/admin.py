from django.contrib import admin

from humotech.accounts.models import User, UserRoleScope
from humotech.core.admin import OperationalAdmin


@admin.register(User)
class UserAdmin(OperationalAdmin):
    """Пароль здесь не показывается и не редактируется.

    В базе лежит только хеш, и поле исключено из формы: правка хеша руками
    даёт учётную запись, в которую нельзя войти, а поле для открытого пароля
    в аварийном интерфейсе — прямой способ его туда и записать.
    """

    list_display = ("email", "organization", "status", "employee",
                    "mfa_enabled", "last_login")
    list_filter = ("status", "mfa_enabled", "organization")
    search_fields = ("email",)
    exclude = ("password",)
    list_select_related = ("organization", "employee")


@admin.register(UserRoleScope)
class UserRoleScopeAdmin(OperationalAdmin):
    """Роль и территория. Пустые регион и офис — доступ ко всей организации."""

    list_display = ("user", "role", "region", "office", "valid_from", "valid_to")
    list_filter = ("role", "organization")
    search_fields = ("user__email",)
    list_select_related = ("user", "role", "region", "office")
