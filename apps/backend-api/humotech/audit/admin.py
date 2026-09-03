from django.contrib import admin

from humotech.audit.models import AuditLog
from humotech.core.admin import ReadOnlyAdmin


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    """Журнал неизменяем по определению: только INSERT, никаких правок.

    Аудит, который можно отредактировать из интерфейса, аудитом не является.
    """

    list_display = ("occurred_at", "action", "entity_type", "entity_id",
                    "actor_user", "organization")
    list_filter = ("action", "entity_type", "organization")
    search_fields = ("entity_id", "action")
    date_hierarchy = "occurred_at"
    list_select_related = ("actor_user", "organization")
