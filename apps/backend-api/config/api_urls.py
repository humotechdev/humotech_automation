"""Маршруты API версии 1.

Версия зафиксирована в пути (`/api/v1/`): клиентов будет несколько —
React CRM, Telegram-бот, Mini App, — и обновляются они не одновременно.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from humotech.accounts.views import CurrentUserView, LoginView, LogoutView
from humotech.analytics.views import (
    AnalyticsView,
    ComparisonView,
    DashboardView,
)
from humotech.attendance.views import (
    AttendanceViewSet,
    CorrectionDecisionView,
    CorrectionListView,
    ManualEventView,
    PresenceView,
)
from humotech.employees.views import EmployeeViewSet
from humotech.absences.views import (
    AbsenceDecisionView,
    PendingAbsenceRequestsView,
)
from humotech.offices.views import OfficeViewSet
from humotech.qr_codes.views import (
    QrDeviceActionView,
    QrDeviceListView,
    QrDisplayCodeView,
    QrDisplayPairView,
    QrPointViewSet,
)
from humotech.regions.views import RegionViewSet
from humotech.schedules.views import EmployeeScheduleViewSet, WorkScheduleViewSet
from humotech.telegram.views import (
    BotLinkView,
    BotOutboxView,
    EmployeeTelegramDisconnectView,
    EmployeeTelegramView,
    MiniAppAuthView,
    MiniAppMeView,
    TelegramInvitationViewSet,
)

router = DefaultRouter()
router.register("regions", RegionViewSet, basename="region")
router.register("offices", OfficeViewSet, basename="office")
router.register("employees", EmployeeViewSet, basename="employee")
router.register("work-schedules", WorkScheduleViewSet, basename="work-schedule")
router.register("qr-points", QrPointViewSet, basename="qr-point")
router.register(
    "telegram/invitations", TelegramInvitationViewSet, basename="telegram-invitation"
)

urlpatterns = [
    path("auth/login", LoginView.as_view(), name="login"),
    path("auth/logout", LogoutView.as_view(), name="logout"),
    path("auth/me", CurrentUserView.as_view(), name="current-user"),
    # История графиков сотрудника: вложенный ресурс, потому что вне сотрудника
    # она смысла не имеет.
    path(
        "employees/<uuid:employee_pk>/schedules",
        EmployeeScheduleViewSet.as_view({"get": "list"}),
        name="employee-schedules",
    ),
    # Состояние привязки Telegram — часть карточки сотрудника: HR смотрит
    # его там же, где всё остальное про человека.
    path(
        "employees/<uuid:employee_pk>/telegram",
        EmployeeTelegramView.as_view(),
        name="employee-telegram",
    ),
    path(
        "employees/<uuid:employee_pk>/telegram/disconnect",
        EmployeeTelegramDisconnectView.as_view(),
        name="employee-telegram-disconnect",
    ),
    # Вход бота. Пользователя за ним нет: обращается сам бот, предъявляя
    # общий секрет, а право на операцию даёт токен приглашения.
    path("telegram/bot/link", BotLinkView.as_view(), name="telegram-bot-link"),
    # Очередь уведомлений. Бот забирает её отсюда, а не из базы: подключения
    # к PostgreSQL у него нет и заводить его ради двух запросов не нужно.
    path("telegram/bot/outbox", BotOutboxView.as_view(), name="telegram-bot-outbox"),
    # Mini App. Путь начинается с /api/v1/telegram/mini-app/ — ровно на этом
    # префиксе работает CORS (см. humotech/telegram/middleware.py).
    path(
        "telegram/mini-app/auth",
        MiniAppAuthView.as_view(),
        name="telegram-mini-app-auth",
    ),
    path(
        "telegram/mini-app/me", MiniAppMeView.as_view(), name="telegram-mini-app-me"
    ),
    # Экраны показа QR. Префикс отдельный: на нём своя зона CORS со своим
    # списком origin'ов — адреса экранов не должны открывать ничего сверх
    # выдачи кодов.
    path("qr-display/pair", QrDisplayPairView.as_view(), name="qr-display-pair"),
    path("qr-display/code", QrDisplayCodeView.as_view(), name="qr-display-code"),
    # Управление экранами из CRM. Сюда экран не ходит, поэтому и адрес
    # другой — вне зоны CORS экранов.
    path("qr/devices", QrDeviceListView.as_view(), name="qr-devices"),
    path(
        "qr/devices/<uuid:device_id>/<str:action>",
        QrDeviceActionView.as_view(),
        name="qr-device-action",
    ),
    # Главная страница CRM: одним запросом вместо тринадцати. Каждая
    # карточка несёт адрес, по которому виден её состав, — число без
    # такого адреса кадровику бесполезно.
    path("dashboard", DashboardView.as_view(), name="dashboard"),
    # Аналитика. Каждая доля приходит с числителем, знаменателем и
    # словесным определением формулы: процент без них проверить нечем.
    path("analytics", AnalyticsView.as_view(), name="analytics"),
    path("analytics/compare", ComparisonView.as_view(), name="analytics-compare"),
    # Посещаемость глазами кадровика. Сырые события отдаются только на
    # чтение: строка отметки не меняется никогда, а исправление — это
    # либо решение по заявке, либо новое событие с source = MANUAL.
    path("attendance/events", AttendanceViewSet.as_view({"get": "events"}),
         name="attendance-events"),
    path("attendance/sessions", AttendanceViewSet.as_view({"get": "sessions"}),
         name="attendance-sessions"),
    path("attendance/presence", PresenceView.as_view(), name="attendance-presence"),
    path("attendance/corrections", CorrectionListView.as_view(),
         name="attendance-corrections"),
    path("attendance/corrections/<uuid:request_id>/<str:decision>",
         CorrectionDecisionView.as_view(), name="attendance-correction-decision"),
    path("attendance/manual", ManualEventView.as_view(), name="attendance-manual"),
    # Решения по заявкам на отсутствие. React-интерфейса для них пока нет —
    # он следующим этапом, — но подтверждать больничные надо уже сейчас.
    path("absence-requests/pending", PendingAbsenceRequestsView.as_view(),
         name="absence-requests-pending"),
    path("absence-requests/<uuid:request_id>/<str:decision>",
         AbsenceDecisionView.as_view(), name="absence-request-decision"),
    # Личный кабинет сотрудника. Один набор endpoint'ов на Mini App и бота:
    # разные клиенты, но одни и те же цифры.
    path("me/", include("humotech.selfservice.urls")),
    path("", include(router.urls)),
]
