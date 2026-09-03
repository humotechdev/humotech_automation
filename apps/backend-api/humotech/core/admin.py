"""Django Admin как аварийный интерфейс.

Это НЕ основная CRM: та будет отдельным React-приложением поверх REST API,
и все обычные операции идут через него — там работают доменные права,
область видимости и журнал аудита.

Админка нужна для другого: посмотреть и поправить данные, когда что-то
пошло не так и обычный путь недоступен. Отсюда её устройство:

  * вход только для `SUPER_ADMIN` (см. `accounts.User.is_staff`);
  * всё открывается на чтение, а изменение разрешено лишь там, где оно
    осмысленно в аварийной ситуации;
  * удаление выключено везде. Историю сотрудников и отметок нельзя
    удалять из интерфейса — на уровне базы это и так запрещено
    внешними ключами, и обходить их через админку тем более незачем.
"""

from __future__ import annotations

from django.contrib import admin


class ReadOnlyAdmin(admin.ModelAdmin):
    """Только просмотр и поиск. Ничего не создаёт и не удаляет."""

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class OperationalAdmin(admin.ModelAdmin):
    """Просмотр и правка, но без удаления.

    Удаление закрыто намеренно: у этих таблиц есть история, и внешние ключи
    с `ON DELETE RESTRICT` всё равно не дадут снести строку со связями —
    кнопка в интерфейсе только создавала бы ложное ожидание.
    """

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


admin.site.site_header = "HUMOTECH — техническая панель"
admin.site.site_title = "HUMOTECH"
admin.site.index_title = "Аварийное управление данными"
