"""Маршруты личного кабинета. Все под `/api/v1/me/`.

Префикс общий не для красоты: на нём стоит зона CORS (`humotech/core/cors.py`)
и на нём же — ограничение частоты. Endpoint сотрудника, оказавшийся вне
`/me/`, тихо остался бы без обоих.

Слэша в конце нет — как и у остальных явных путей проекта.
"""

from django.urls import path

from humotech.selfservice.absences import (
    AbsenceApplicationView,
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
from humotech.selfservice.ask import AskView, EscalateView
from humotech.selfservice.questions import QuestionMessageView
from humotech.selfservice.surveys import SurveyListView, SurveyView
from humotech.onboarding.self_views import (
    OnboardingAcknowledgeView,
    OnboardingDecisionView,
    OnboardingSectionView,
    OnboardingStartView,
    OnboardingStateView,
    PolicyFileView,
    PolicyTextView,
)
from humotech.selfservice.views import (
    HistoryView,
    ProfileView,
    DayNoticeView,
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
    # Ответ на напоминание о начале дня. Отдельно от отметки: это
    # не факт присутствия, а объяснение его отсутствия.
    path("attendance/notice", DayNoticeView.as_view(), name="self-day-notice"),
    # Вопрос ассистенту и передача его HR — два отдельных действия.
    # Обращение в CRM заводится только вторым: очередь, забитая тем,
    # что решилось само, перестаёт быть очередью.
    path("ask", AskView.as_view(), name="self-ask"),
    path("ask/escalate", EscalateView.as_view(), name="self-ask-escalate"),
    # Больничные и отпуска — одна механика с разными видами отсутствия.
    # Два набора endpoint'ов означали бы два места, где чинить одну ошибку.
    path("absences", AbsenceListView.as_view(), name="self-absences"),
    path("absences/options", AbsenceOptionsView.as_view(),
         name="self-absence-options"),
    path("absences/<uuid:request_id>", AbsenceDetailView.as_view(),
         name="self-absence"),
    path("absences/<uuid:request_id>/extend", AbsenceExtendView.as_view(),
         name="self-absence-extend"),
    # Заявление для печати. Собирается из заявки на каждое
    # обращение: сохранённый бланк разошёлся бы с продлением.
    path("absences/<uuid:request_id>/application", AbsenceApplicationView.as_view(),
         name="self-absence-application"),
    path("absences/<uuid:request_id>/document", AbsenceDocumentView.as_view(),
         name="self-absence-document"),
    path("leave-balance", LeaveBalanceView.as_view(), name="self-leave-balance"),
    # Опросы. Вопросы отдаются целиком одним ответом: опрос короткий, и
    # запрос на каждый экран означал бы белый экран на каждом «Далее».
    path("surveys", SurveyListView.as_view(), name="self-surveys"),
    path("surveys/<uuid:recipient_id>", SurveyView.as_view(), name="self-survey"),
    # Вопрос в HR. Сообщение ложится в обращение по правилам очереди.
    path("questions/messages", QuestionMessageView.as_view(),
         name="self-question-message"),
    # Первичное ознакомление. Эти адреса — единственные, что открыты до
    # его завершения: гейт закрывает рабочие функции, а не дорогу к их
    # открытию.
    path("onboarding", OnboardingStateView.as_view(), name="self-onboarding"),
    path("onboarding/start", OnboardingStartView.as_view(),
         name="self-onboarding-start"),
    # Раздел по НОМЕРУ: так устроены кнопки «← Назад», и так короче
    # полезная нагрузка кнопки в Telegram.
    path("onboarding/sections/<int:position>", OnboardingSectionView.as_view(),
         name="self-onboarding-section"),
    path("onboarding/acknowledge", OnboardingAcknowledgeView.as_view(),
         name="self-onboarding-acknowledge"),
    path("onboarding/decision", OnboardingDecisionView.as_view(),
         name="self-onboarding-decision"),
    # Полный текст и файл — отдельными запросами: правовой текст длинный,
    # и таскать его на каждое обновление экрана незачем.
    path("policies/<uuid:version_id>", PolicyTextView.as_view(),
         name="self-policy-text"),
    path("policies/<uuid:version_id>/file", PolicyFileView.as_view(),
         name="self-policy-file"),
    # Уведомления только читаются и отмечаются прочитанными: заводит их
    # система по событиям, а не сотрудник.
    path("notifications", NotificationListView.as_view(),
         name="self-notifications"),
    path("notifications/<uuid:notification_id>/read",
         NotificationReadView.as_view(), name="self-notification-read"),
]
