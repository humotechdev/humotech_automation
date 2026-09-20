"""Маршруты API версии 1.

Версия зафиксирована в пути (`/api/v1/`): клиентов будет несколько —
React CRM, Telegram-бот, Mini App, — и обновляются они не одновременно.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from humotech.accounts.views import CurrentUserView, LoginView, LogoutView
from humotech.accounts.rbac_views import (
    AssignableScopesView,
    CrmUserViewSet,
    GrantCreateView,
    GrantDetailView,
    PermissionListView,
    RoleDetailView,
    RoleListView,
    UserGrantsView,
)
from humotech.audit.views import AuditLogView
from humotech.core.queue_views import RequestCountsView, RequestQueueView
from humotech.analytics.views import (
    AnalyticsOverviewView,
    AnalyticsView,
    ComparisonView,
    DashboardView,
    MovementView,
)
from humotech.attendance.views import (
    AttendanceViewSet,
    EmployeeDailyView,
    CorrectionDecisionView,
    CorrectionListView,
    ManualEventView,
    PresenceView,
)
from humotech.departments.views import DepartmentViewSet, PositionViewSet
from humotech.employees.views import EmployeeViewSet
from humotech.absences.type_views import AbsenceTypeViewSet
from humotech.absences.views import (
    AbsenceApplicationView,
    AbsenceDocumentDecisionView,
    AbsenceDecisionView,
    AbsenceDocumentDownloadView,
    PendingAbsenceRequestsView,
)
from humotech.knowledge.views import (
    FaqViewSet,
    KnowledgeIndexJobViewSet,
    KnowledgeSourceViewSet,
)
from humotech.notifications.feed_views import (
    FeedCountsView,
    FeedItemView,
    FeedReadAllView,
    FeedView,
)
from humotech.notifications.views import NotificationViewSet
from humotech.offices.views import OfficeViewSet
from humotech.onboarding.views import (
    EmployeeOnboardingActionView,
    EmployeeOnboardingView,
    OnboardingCountsView,
    OnboardingExportView,
    OnboardingProgressView,
    OnboardingSectionViewSet,
    PolicyDocumentViewSet,
    PolicyVersionFileView,
    PolicyVersionPublishView,
    PolicyVersionView,
)
from humotech.organizations.views import (
    IntegrationsView,
    OrganizationSettingDetailView,
    OrganizationSettingsView,
)
from humotech.qr_codes.views import (
    QrDeviceActionView,
    QrDeviceListView,
    QrDisplayCodeView,
    QrDisplayPairView,
    QrPointViewSet,
)
from humotech.questions.views import (
    EscalationViewSet,
    UnansweredQuestionViewSet,
)
from humotech.regions.views import RegionViewSet
from humotech.reports.builder_views import (
    ReportCatalogView,
    ReportPreviewView,
    ReportTemplateViewSet,
)
from humotech.reports.job_views import ExportJobViewSet
from humotech.reports.views import ExportView
from humotech.schedules.calendar_views import CalendarExceptionViewSet
from humotech.schedules.views import EmployeeScheduleViewSet, WorkScheduleViewSet
from humotech.surveys.views import SurveyCampaignViewSet, SurveyTemplateViewSet
from humotech.telegram.views import (
    BotLinkAcceptView,
    BotLinkView,
    BotRecognizeView,
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
router.register("departments", DepartmentViewSet, basename="department")
router.register("positions", PositionViewSet, basename="position")
# Справочник причин отсутствия. Удаления у него нет: вид живёт в истории
# заявок, убирают его выключением.
router.register("absence-types", AbsenceTypeViewSet, basename="absence-type")
router.register("users", CrmUserViewSet, basename="crm-user")
# Очередь уведомлений глазами кадровика: почему сообщение не дошло
# и как отправить его снова. Отправляет по-прежнему бот.
router.register("notifications", NotificationViewSet, basename="notification")
# База знаний. Документы и FAQ ведутся при выключенном ассистенте —
# отказывают только индексация и включение FAQ в поиск: посчитать
# эмбеддинг нечем, а подставить вместо него случайный нельзя.
router.register(
    "knowledge/sources", KnowledgeSourceViewSet, basename="knowledge-source"
)
router.register("knowledge/faq", FaqViewSet, basename="knowledge-faq")
router.register(
    "knowledge/index-jobs", KnowledgeIndexJobViewSet,
    basename="knowledge-index-job",
)
# Два разных списка под похожими названиями: кластеры формулировок для
# пополнения базы знаний и обращения конкретных людей, ждущих ответа.
router.register(
    "knowledge/unanswered-questions", UnansweredQuestionViewSet,
    basename="unanswered-question",
)
router.register(
    "knowledge/escalations", EscalationViewSet, basename="escalation"
)
# Фоновые выгрузки. Мгновенная выгрузка ниже остаётся: короткий отчёт
# незачем прогонять через заказ, ожидание и скачивание.
router.register("export-jobs", ExportJobViewSet, basename="export-job")
# Личные шаблоны конструктора отчётов.
router.register("report-templates", ReportTemplateViewSet, basename="report-template")
router.register(
    "calendar-exceptions", CalendarExceptionViewSet, basename="calendar-exception"
)
router.register(
    "telegram/invitations", TelegramInvitationViewSet, basename="telegram-invitation"
)
# Опросы сотрудников. Шаблон — набор вопросов, который переиспользуют;
# рассылка — одно обращение к названному кругу людей. Разделены не для
# симметрии: правка шаблона не должна менять то, что уже спросили.
router.register(
    "surveys/templates", SurveyTemplateViewSet, basename="survey-template"
)
router.register(
    "surveys/campaigns", SurveyCampaignViewSet, basename="survey-campaign"
)
# Первичное ознакомление. Разделы и документы — разные справочники:
# карточка рассказывает о компании, документ обязывает, и версионируются
# они по-разному.
router.register(
    "onboarding/sections", OnboardingSectionViewSet, basename="onboarding-section"
)
router.register(
    "onboarding/documents", PolicyDocumentViewSet, basename="policy-document"
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
    # Первичное ознакомление одного человека — там же, где всё остальное
    # про него. Действие стоит в пути, а не в теле: каждое из них —
    # отдельное решение с отдельной записью в журнале.
    path(
        "employees/<uuid:employee_pk>/onboarding",
        EmployeeOnboardingView.as_view(),
        name="employee-onboarding",
    ),
    path(
        "employees/<uuid:employee_pk>/onboarding/<str:action>",
        EmployeeOnboardingActionView.as_view(),
        name="employee-onboarding-action",
    ),
    # Сводка по всем. `counts` и `export` стоят ВЫШЕ общего маршрута
    # разделов: путь читается сверху вниз.
    path(
        "onboarding/progress",
        OnboardingProgressView.as_view(),
        name="onboarding-progress",
    ),
    path(
        "onboarding/counts", OnboardingCountsView.as_view(),
        name="onboarding-counts",
    ),
    path(
        "onboarding/export", OnboardingExportView.as_view(),
        name="onboarding-export",
    ),
    # Редакция документа. Публикация вынесена отдельным адресом: это не
    # правка полей, а решение, закрывающее бота всем, кто её не принял.
    path(
        "onboarding/versions/<uuid:version_id>",
        PolicyVersionView.as_view(), name="policy-version",
    ),
    path(
        "onboarding/versions/<uuid:version_id>/publish",
        PolicyVersionPublishView.as_view(), name="policy-version-publish",
    ),
    path(
        "onboarding/versions/<uuid:version_id>/file",
        PolicyVersionFileView.as_view(), name="policy-version-file",
    ),
    # Вход бота. Пользователя за ним нет: обращается сам бот, предъявляя
    # общий секрет, а право на операцию даёт токен приглашения.
    path("telegram/bot/link", BotLinkView.as_view(), name="telegram-bot-link"),
    path("telegram/bot/link/accept", BotLinkAcceptView.as_view(), name="telegram-bot-link-accept"),
    # Узнавание по имени в Telegram: когда человек открыл бота сам, без
    # ссылки. Привязка получается такой же ожидающей подтверждения.
    path(
        "telegram/bot/recognize",
        BotRecognizeView.as_view(),
        name="telegram-bot-recognize",
    ),
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
    # Общая очередь заявок: отсутствия и исправления отметок одним
    # списком. Склеить две страницы на клиенте нельзя — получилась бы
    # не очередь, а произвольная смесь двух её половин.
    # Лента событий кадровика. Не очередь отправки (`notifications`):
    # та отвечает, ушло ли сообщение сотруднику, а эта — что случилось
    # в кадровом контуре и ждёт человека.
    path("notification-feed", FeedView.as_view(), name="notification-feed"),
    path(
        "notification-feed/counts",
        FeedCountsView.as_view(),
        name="notification-feed-counts",
    ),
    path(
        "notification-feed/read-all",
        FeedReadAllView.as_view(),
        name="notification-feed-read-all",
    ),
    # Ключ события составной («вид:запись»), поэтому в адресе он
    # строкой: UUID-конвертер такой ключ не пропустит.
    path(
        "notification-feed/<str:event_id>",
        FeedItemView.as_view(),
        name="notification-feed-item",
    ),
    path("requests", RequestQueueView.as_view(), name="requests-queue"),
    # Счётчики вкладок очереди одним ответом: по одной строке на вкладку
    # видно только «есть или нет», а не сколько.
    path("requests/counts", RequestCountsView.as_view(), name="requests-counts"),
    # Аналитика. Каждая доля приходит с числителем, знаменателем и
    # словесным определением формулы: процент без них проверить нечем.
    path("analytics", AnalyticsView.as_view(), name="analytics"),
    path("analytics/compare", ComparisonView.as_view(), name="analytics-compare"),
    # Движение сотрудников. Отдельно от посещаемости: там единица
    # измерения — дни, здесь — люди.
    path("analytics/movement", MovementView.as_view(), name="analytics-movement"),
    # Всё для страницы «Аналитика» одним ответом: сводка с прошлым
    # периодом, дни, рейтинг офисов, ритм прихода, дни недели.
    path("analytics/overview", AnalyticsOverviewView.as_view(), name="analytics-overview"),
    # Выгрузки. Право reports.export проверяется отдельно от прав на сами
    # данные: выгрузка не должна быть обходным путём к закрытому экрану.
    # Конструктор отчётов: поля видов и предпросмотр до заказа файла.
    path("reports/catalog", ReportCatalogView.as_view(), name="report-catalog"),
    path("reports/preview", ReportPreviewView.as_view(), name="report-preview"),
    path("reports/<str:kind>/export", ExportView.as_view(), name="report-export"),
    # Журнал изменений. Только чтение: метода записи здесь нет намеренно,
    # единственный способ появиться в журнале — быть записанным сервисом,
    # который выполняет само действие.
    path("audit-logs", AuditLogView.as_view(), name="audit-logs"),
    path("roles", RoleListView.as_view(), name="roles"),
    path("roles/<uuid:role_id>", RoleDetailView.as_view(), name="role-detail"),
    # Справочник разрешений. Только чтение: список операций системы задан
    # миграциями, а не настройкой организации.
    path("permissions", PermissionListView.as_view(), name="permissions"),
    path(
        "users/<uuid:user_id>/grants",
        UserGrantsView.as_view(),
        name="user-grants",
    ),
    # Области, доступные для выдачи. Стоит перед `grants/<uuid>`
    # намеренно: путь читается сверху вниз, и общий маршрут с
    # идентификатором не должен перехватывать именованный.
    path(
        "grants/scopes",
        AssignableScopesView.as_view(),
        name="grant-scopes",
    ),
    path("grants", GrantCreateView.as_view(), name="grant-create"),
    path(
        "grants/<uuid:grant_id>",
        GrantDetailView.as_view(),
        name="grant-detail",
    ),
    path("settings", OrganizationSettingsView.as_view(), name="settings"),
    # Состояние подключений. Стоит перед `settings/<key>`: путь
    # читается сверху вниз, и общий маршрут перехватил бы именованный.
    path(
        "settings/integrations",
        IntegrationsView.as_view(),
        name="settings-integrations",
    ),
    path(
        "settings/<str:key>",
        OrganizationSettingDetailView.as_view(),
        name="setting-detail",
    ),
    # Посещаемость глазами кадровика. Сырые события отдаются только на
    # чтение: строка отметки не меняется никогда, а исправление — это
    # либо решение по заявке, либо новое событие с source = MANUAL.
    path("attendance/events", AttendanceViewSet.as_view({"get": "events"}),
         name="attendance-events"),
    path("attendance/sessions", AttendanceViewSet.as_view({"get": "sessions"}),
         name="attendance-sessions"),
    path("attendance/presence", PresenceView.as_view(), name="attendance-presence"),
    # Журнал по дням для одного человека. Строку дня собирает тот же
    # код, что и присутствие: у ночной смены и открытой сессии
    # должен быть один ответ, а не два похожих.
    path(
        "attendance/daily",
        EmployeeDailyView.as_view(),
        name="attendance-daily",
    ),
    path("attendance/corrections", CorrectionListView.as_view(),
         name="attendance-corrections"),
    path("attendance/corrections/<uuid:request_id>/<str:decision>",
         CorrectionDecisionView.as_view(), name="attendance-correction-decision"),
    path("attendance/manual", ManualEventView.as_view(), name="attendance-manual"),
    # Решения по заявкам на отсутствие. React-интерфейса для них пока нет —
    # он следующим этапом, — но подтверждать больничные надо уже сейчас.
    path("absence-requests/pending", PendingAbsenceRequestsView.as_view(),
         name="absence-requests-pending"),
    # Заявление для печати. Раньше маршрута с <str:decision>: иначе
    # «application» разобралось бы как решение по заявке.
    path("absence-requests/<uuid:request_id>/application",
         AbsenceApplicationView.as_view(), name="absence-request-application"),
    path("absence-requests/<uuid:request_id>/<str:decision>",
         AbsenceDecisionView.as_view(), name="absence-request-decision"),
    path("absence-requests/<uuid:request_id>/documents/<uuid:document_id>/download",
         AbsenceDocumentDownloadView.as_view(),
         name="absence-request-document-download"),
    # Решение по справке. Отдельно от решения по заявке: одобренный
    # больничный с отклонённой справкой — законное состояние.
    path("absence-requests/<uuid:request_id>/documents/<uuid:document_id>/"
         "<str:decision>",
         AbsenceDocumentDecisionView.as_view(),
         name="absence-request-document-decision"),
    # Личный кабинет сотрудника. Один набор endpoint'ов на Mini App и бота:
    # разные клиенты, но одни и те же цифры.
    path("me/", include("humotech.selfservice.urls")),
    path("", include(router.urls)),
]
