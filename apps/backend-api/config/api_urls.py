"""Маршруты API версии 1.

Версия зафиксирована в пути (`/api/v1/`): клиентов будет несколько —
React CRM, Telegram-бот, Mini App, — и обновляются они не одновременно.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from humotech.accounts.views import CurrentUserView, LoginView, LogoutView
from humotech.employees.views import EmployeeViewSet
from humotech.offices.views import OfficeViewSet
from humotech.regions.views import RegionViewSet
from humotech.schedules.views import EmployeeScheduleViewSet, WorkScheduleViewSet
from humotech.telegram.views import (
    BotLinkView,
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
    path("", include(router.urls)),
]
