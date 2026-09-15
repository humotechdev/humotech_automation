"""Маршруты личного кабинета. Все под `/api/v1/me/`.

Префикс общий не для красоты: на нём стоит зона CORS (`humotech/core/cors.py`)
и на нём же — ограничение частоты. Endpoint сотрудника, оказавшийся вне
`/me/`, тихо остался бы без обоих.

Слэша в конце нет — как и у остальных явных путей проекта.
"""

from django.urls import path

from humotech.selfservice.absences import (
    AbsenceDetailView,
    AbsenceDocumentView,
    AbsenceExtendView,
    AbsenceListView,
    AbsenceOptionsView,
    LeaveBalanceView,
)
from humotech.selfservice.notifications import (
    NotificationListView,
    NotificationReadView,
)
from humotech.selfservice.questions import QuestionMessageView
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
    # Больничные и отпуска — одна механика с разными видами отсутствия.
    # Два набора endpoint'ов означали бы два места, где чинить одну ошибку.
    path("absences", AbsenceListView.as_view(), name="self-absences"),
    path("absences/options", AbsenceOptionsView.as_view(),
         name="self-absence-options"),
    path("absences/<uuid:request_id>", AbsenceDetailView.as_view(),
         name="self-absence"),
    path("absences/<uuid:request_id>/extend", AbsenceExtendView.as_view(),
         name="self-absence-extend"),
    path("absences/<uuid:request_id>/document", AbsenceDocumentView.as_view(),
         name="self-absence-document"),
    path("leave-balance", LeaveBalanceView.as_view(), name="self-leave-balance"),
    # Вопрос в HR. Сообщение ложится в обращение по правилам очереди.
    path("questions/messages", QuestionMessageView.as_view(),
         name="self-question-message"),
    # Уведомления только читаются и отмечаются прочитанными: заводит их
    # система по событиям, а не сотрудник.
    path("notifications", NotificationListView.as_view(),
         name="self-notifications"),
    path("notifications/<uuid:notification_id>/read",
         NotificationReadView.as_view(), name="self-notification-read"),
]
