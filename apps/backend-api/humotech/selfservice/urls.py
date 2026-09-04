"""Маршруты личного кабинета. Все под `/api/v1/me/`.

Префикс общий не для красоты: на нём стоит зона CORS (`humotech/core/cors.py`)
и на нём же — ограничение частоты. Endpoint сотрудника, оказавшийся вне
`/me/`, тихо остался бы без обоих.

Слэша в конце нет — как и у остальных явных путей проекта.
"""

from django.urls import path

from humotech.selfservice.views import ProfileView, ScanView

urlpatterns = [
    path("profile", ProfileView.as_view(), name="self-profile"),
    path("attendance/scan", ScanView.as_view(), name="self-scan"),
]
