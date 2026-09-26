"""Корневая маршрутизация.

Версия API зафиксирована в пути (`/api/v1/`): клиентов будет несколько —
React CRM, Telegram-бот, Mini App, — и обновляются они не одновременно.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from humotech.core.views import healthz, readyz

# Ошибки вне DRF (несуществующий путь, SuspiciousOperation, необработанное
# исключение) — тем же JSON-конвертом, что и остальной API, и без
# трейсбеков. См. `humotech/core/views.py`.
handler400 = "humotech.core.views.bad_request"
handler403 = "humotech.core.views.permission_denied"
handler404 = "humotech.core.views.page_not_found"
handler500 = "humotech.core.views.server_error"

urlpatterns = [
    # Django Admin — закрытый технический интерфейс, не основная CRM.
    path("admin/", admin.site.urls),
    path("api/v1/", include(("config.api_urls", "api"), namespace="v1")),
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    # Машиночитаемая схема доступна всегда: по ней фронтенд генерирует
    # типы, и в рабочем окружении она нужна ровно так же, как в разработке.
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
]

# А вот интерактивные страницы — только при DEBUG. Это удобные читалки,
# а не часть продукта: в бою они лишний открытый интерфейс, который надо
# защищать наравне с остальными.
if settings.DEBUG:
    urlpatterns += [
        path(
            "api/docs",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger",
        ),
        path(
            "api/redoc",
            SpectacularRedocView.as_view(url_name="schema"),
            name="redoc",
        ),
    ]
