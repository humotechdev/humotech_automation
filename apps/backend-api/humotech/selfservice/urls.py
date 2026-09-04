"""Маршруты личного кабинета. Все под `/api/v1/me/`.

Префикс общий не для красоты: на нём стоит зона CORS (`humotech/core/cors.py`)
и на нём же — ограничение частоты. Endpoint сотрудника, оказавшийся вне
`/me/`, тихо остался бы без обоих.

Слэша в конце нет — как и у остальных явных путей проекта.
"""

from django.urls import path

from humotech.selfservice.views import (
    HistoryView,
    ProfileView,
    ScanView,
    StatisticsView,
    StatusView,
)

urlpatterns = [
    path("profile", ProfileView.as_view(), name="self-profile"),
    path("status", StatusView.as_view(), name="self-status"),
    path("statistics", StatisticsView.as_view(), name="self-statistics"),
    path("history", HistoryView.as_view(), name="self-history"),
    path("attendance/scan", ScanView.as_view(), name="self-scan"),
]
