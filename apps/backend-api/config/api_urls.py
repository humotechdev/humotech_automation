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

router = DefaultRouter()
router.register("regions", RegionViewSet, basename="region")
router.register("offices", OfficeViewSet, basename="office")
router.register("employees", EmployeeViewSet, basename="employee")
router.register("work-schedules", WorkScheduleViewSet, basename="work-schedule")

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
    path("", include(router.urls)),
]
