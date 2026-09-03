from django.contrib import admin

from humotech.core.admin import OperationalAdmin
from humotech.regions.models import Region


@admin.register(Region)
class RegionAdmin(OperationalAdmin):
    list_display = ("code", "name", "organization", "status", "timezone")
    list_filter = ("status", "organization")
    search_fields = ("code", "name")
    ordering = ("organization", "code")
