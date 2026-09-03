"""Корневая маршрутизация.

Версия API зафиксирована в пути (`/api/v1/`): клиентов будет несколько —
React CRM, Telegram-бот, Mini App, — и обновляются они не одновременно.
"""

from django.contrib import admin
from django.urls import include, path

from humotech.core.views import healthz, readyz

urlpatterns = [
    # Django Admin — закрытый технический интерфейс, не основная CRM.
    path("admin/", admin.site.urls),
    path("api/v1/", include(("config.api_urls", "api"), namespace="v1")),
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
]
