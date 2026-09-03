from django.contrib import admin

from humotech.core.admin import OperationalAdmin
from humotech.offices.models import Office, OfficeNetwork


@admin.register(Office)
class OfficeAdmin(OperationalAdmin):
    list_display = ("code", "name", "region", "status", "timezone", "address")
    list_filter = ("status", "organization", "region")
    search_fields = ("code", "name", "address")
    ordering = ("organization", "code")
    # регион подтягивается тем же запросом: иначе список офисов даёт
    # по запросу на строку
    list_select_related = ("region",)


@admin.register(OfficeNetwork)
class OfficeNetworkAdmin(OperationalAdmin):
    list_display = ("office", "name", "network_cidr", "is_active")
    list_filter = ("is_active", "organization")
    search_fields = ("name",)
    list_select_related = ("office",)
