from django.contrib import admin

from humotech.core.admin import OperationalAdmin
from humotech.organizations.models import Organization, OrganizationSetting


@admin.register(Organization)
class OrganizationAdmin(OperationalAdmin):
    list_display = ("code", "name", "status", "default_timezone",
                    "knowledge_revision", "created_at")
    list_filter = ("status",)
    search_fields = ("code", "name")
    ordering = ("code",)


@admin.register(OrganizationSetting)
class OrganizationSettingAdmin(OperationalAdmin):
    list_display = ("organization", "key", "updated_at")
    list_filter = ("organization",)
    search_fields = ("key",)
