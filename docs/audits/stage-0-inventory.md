# Этап 0. Инвентаризация системы HUMOTECH (по коду, состояние на 2026-10-11, ветка security/fix-2026-10-11)

## Оглавление
1. Компоненты и границы доверия
2. Модули бэкенда
3. Карта API
4. Модель данных
5. Статусные машины
6. Роли и права
7. Аналитика: где считаются показатели
8. Тесты
9. Опасные операции и точки входа
10. Расхождения «документация и код»
11. Наблюдения для следующих этапов

Пути даны относительно `D:\HUMO\apps\backend-api\`, если не сказано иное.

---

## 1. Компоненты и границы доверия

### 1.1 Стеки (три разных compose-файла)
| Стек | Файл | Состав |
|---|---|---|
| Локальный | `infrastructure/docker/docker-compose.yml` содержит ТОЛЬКО postgres (pgvector/pgvector:pg18). Реальный стек — надстройка `docker-compose.local.yml`: postgres, migrate, backend, events, bot, export-worker, attendance-reminders, onboarding-reminders, gateway, ngrok | вход снаружи — ngrok на gateway:80 |
| Тест-сервер | `deploy/test-server/docker-compose.yml` | postgres, migrate, backend, events, export-worker, attendance-reminders, onboarding-reminders, bot, gateway, crm, caddy |
| e2e | `infrastructure/docker/docker-compose.e2e.yml` | postgres-e2e, migrate-e2e, backend-e2e, worker-e2e (бот с заглушкой-отправщиком), export-worker-e2e. Сеть `internal: true`, секреты стенда свои |

Сети тест-сервера (`deploy/test-server/docker-compose.yml:426-440`):
- `edge` — caddy, gateway, crm; у Caddy закреплён IP;
- `app` — gateway, backend, bot, events;
- `db` — `internal: true`, postgres и все, кто ходит в базу.

Контейнеры `export-worker`, `attendance-reminders`, `onboarding-reminders`, `migrate` сидят только в сети `db`. Bot — только в `app`.

### 1.2 Кто с кем говорит
- **Браузер CRM → Caddy (`deploy/test-server/Caddyfile:60-86`).** `{CRM_DOMAIN}`: `/api/v1/events/*` и `/api/*` идут на `gateway:80`, остальное на `crm:80`. Статику CRM раздаёт nginx (`deploy/test-server/crm/nginx.conf`) с CSP и `frame-ancestors 'none'`.
- **Шлюз (`infrastructure/docker/gateway/nginx.conf`).**
  - `:80` — внешний вход.
    - `/api/` → `backend:8000`.
    - `/api/v1/events/` → `events:8001` (без буферизации, таймаут 3600 с).
    - `/api/v1/telegram/bot/` → 404, если есть заголовок X-Forwarded-For («пришло снаружи»); иначе проксируется на backend.
    - `/admin/` — только с адресов из `GATEWAY_ADMIN_ALLOW`.
    - `/` — статика Mini App, на ней CSP Mini App.
  - `:8080` — внутренний вход бота: только `/api/`, а `/api/v1/telegram/bot/outbox/wait` идёт на `events`. Порт не публикуется.
  - X-Forwarded-For принимается только от `GATEWAY_TRUSTED_PROXIES` (nginx.conf:29-31). Тогда у backend `TRUSTED_PROXY_COUNT=1`.
  - Заголовки `X-Bot-Token` и `X-Telegram-User-Id` вырезаются у запросов «снаружи» на всех путях (nginx.conf:116-127).
- **Mini App → backend.** Тот же origin через шлюз (CORS закрыт, `TELEGRAM_MINI_APP_ALLOWED_ORIGINS=""`).
  1. `POST /telegram/mini-app/auth` меняет `initData` (подпись токеном бота, `telegram/initdata.py:107`) на внутренний подписанный токен (`telegram/tokens.py:106,164`, жизнь 43200 с, `settings/base.py:385`).
  2. Дальше `Authorization: Bearer` (`telegram/auth.py:91`). Привязка перечитывается на каждый запрос.
- **Бот → backend.** `BACKEND_API_URL=http://gateway:8080/api/v1`. Два режима (`telegram/auth.py`):
  - заголовки `X-Bot-Token` + `X-Telegram-User-Id` — бот действует за сотрудника на `/me/*` (`BotEmployeeAuthentication`, :142);
  - только `X-Bot-Token` (`IsTelegramBot`, :193) — служебные пути: link, link/accept, recognize, outbox, field-work decision.
  - Секрет сравнивается за постоянное время (`telegram/compare.py`).
- **Экран QR → backend.** `POST /qr-display/pair` (одноразовый код), затем `GET /qr-display/code` с credential экрана. Аутентификации DRF нет (`authentication_classes = []`, `qr_codes/views.py:68,104`); своя зона CORS (`core/cors.py`).
- **CRM → backend.** Сессия Django (`SessionAuthentication`, `settings/base.py:249-252`) + CSRF (cookie читается скриптом, :342). Сессия в БД, скользящие 12 ч, потолок 24 ч (`:330,334`).
- **Воркеры → БД напрямую** (без HTTP): через роль `humotech_app` без DDL (`deploy/test-server/initdb/sql/02_least_privilege.sql`). Миграции идут ролью `humotech_owner`.

### 1.3 Фоновые процессы и команды
| Что | Как запускается | Файл |
|---|---|---|
| `run_export_worker --loop=5` | постоянный контейнер export-worker, heartbeat `reports/heartbeat.py` | `reports/management/commands/run_export_worker.py`, `reports/worker.py` |
| `attendance_reminders --loop=300` | контейнер attendance-reminders (heartbeat порог 900 с) | `attendance/management/commands/attendance_reminders.py`, `attendance/reminders.py:152 run_once` |
| `onboarding_reminders --loop=3600` | контейнер onboarding-reminders (порог 7500 с) | `onboarding/management/commands/onboarding_reminders.py`, `onboarding/reminders.py` |
| События CRM (SSE) и ожидание очереди ботом | uvicorn `config.asgi:application`, порт 8001, 1 воркер, один `LISTEN` на процесс | `realtime/views.py:150 question_events`, `:270 outbox_wait`; `realtime/broker.py`; `realtime/signals.py` |
| Выдача и уборка очереди уведомлений, опросы по сроку | вызывается из `GET /telegram/bot/outbox` | `telegram/views.py:500-558`: `reclaim_stale`, `dispatch_due`, `remind_due`, `run_due`, `claim` |
| Отправка в Telegram | процесс бота (`notifications/worker.py`, `notifications/sender.py`) | отчёт идёт `POST /telegram/bot/outbox` |
| `purge_exports` | вручную (по доке `docs/architecture/background-jobs.md:111`); в compose-файлах не найдено | `reports/management/commands/purge_exports.py` |
| `index_knowledge` | вручную (`docs/local-always-on.md:109`, `background-jobs.md:142`); в compose нет | `ai_assistant/management/commands/index_knowledge.py`, `ai_assistant/worker.py` |
| Демо и тестовые сиды | вручную: `seed`, `seed_demo_crm`, `seed_demo_hr_data`, `seed_onboarding`, `seed_pentest_data`, `seed_demo_notifications`, `demo_data`, `e2e_admin`, `import_demo_photos`, `restore_deleted_notifications`, `telegram_link`, `qr_display_device` | `humotech/**/management/commands/` |
| Резервное копирование | systemd-таймеры и скрипты | `infrastructure/backup/*` (`humotech-backup.timer`, `humotech-restore-test.timer`) |

### 1.4 Доверительные границы (кратко)
1. Интернет → Caddy/ngrok: TLS, лимит тела 25 МБ, `-Server -Via`.
2. Edge → gateway: gateway верит XFF только от IP Caddy.
3. Gateway → backend: XFF перезаписывается одним адресом (`proxy-headers.inc`). Пути бота доступны только без XFF.
4. Бот → backend: общий секрет, личность сотрудника сообщает бот (число из заголовка).
5. Mini App → backend: подписанный токен + перечитывание привязки.
6. Backend → БД: роль `humotech_app`. Файлы (справки, выгрузки) лежат вне MEDIA_URL (`FILES_PRIVATE_ROOT=/app/private-media`, `EXPORTS_PRIVATE_ROOT`), отдаёт их только view.
7. Нет исходящих вызовов backend→Telegram: токен бота нужен backend только для проверки `initData`.

---

## 2. Модули бэкенда (`humotech/*`)

Приложения (`config/settings/base.py:~150-198`): core, organizations, regions, offices, departments, positions, employees, accounts, rbac, devices, files, qr_codes, schedules, attendance, absences, telegram, notifications, questions, audit, reports, knowledge, ai_assistant, surveys, onboarding; без моделей — analytics, realtime, selfservice.

Общая основа: `core/models.py` (UUIDPrimaryKeyModel, CreatedAtModel, TimestampedModel, ArchivableModel, OrganizationScopedModel), `core/service.py` (`BaseService`: `access`, `audit`, `atomic`; `refuse_stale`), `core/api.py` (`ServiceViewSet`, парсеры query), `core/errors.py`, `core/exceptions.py`, `core/pagination.py` (keyset), `core/rbac.py`, `core/enums.py`, `core/constraints.py`, `core/timeframes.py`.

| Модуль | Назначение | Основные модели | Сервисы (файл) | Зависит от (импорты сервисов или моделей других модулей) |
|---|---|---|---|---|
| organizations | организация и настройки | Organization(status), OrganizationSetting(key) | `settings_service.py` | absences, files, audit, ai_assistant, notifications |
| regions | регионы | Region | `regions/services.py` | — |
| offices | офисы, геозона, сети | Office(status, geofence_kind, pending_location), OfficeNetwork | `offices/services.py`, `offices/geo.py` | organizations |
| departments | отделы и должности (в одном сервисе) | Department, Position (`positions/models.py`) | `departments/services.py` | employees, positions |
| employees | карточка, назначения, документы, приём | Employee, EmployeeAssignment, EmployeeOfficeAccess, EmployeeDocument, EmployeeOnboardingKey | `services.py`, `onboarding.py` (приём), `attachments.py`, `lifecycle.py`, `selectors.py` | departments, offices, positions, schedules, telegram, files, notifications, onboarding |
| accounts | пользователи CRM, вход, гранты | User, UserRoleScope, AuthRateCounter | `rbac_service.py`, `security.py`, `backends.py`, `selectors.py` | employees (selectors), offices, regions, rbac |
| rbac | роли и права | Role, Permission, RolePermission | — (миграции и `core/permissions_catalog.py`) | — |
| devices | устройства сотрудников | EmployeeDevice(status) | нет сервиса | — |
| files | приватное хранилище | File(scan_status) | `storage.py`, `serving.py` | — |
| qr_codes | QR-точки, экраны, коды | OfficeQrPoint, QrDisplayDevice, QrDisplaySession | `points.py`, `services.py`, `tokens.py`, `stickers.py` | attendance (при удалении точки: `points.py:90,444`) |
| schedules | графики и календарь | WorkSchedule, ScheduleDay, ScheduleBreak, EmployeeScheduleAssignment, CalendarException | `services.py`, `calendar.py` | departments, employees, attendance, absences |
| attendance | отметки, сессии, выезды, исправления | AttendanceEvent, AttendanceSession, FieldWorkRequest, AttendanceCorrectionRequest, DayNotice | `scanning.py`, `services.py` (register_scan), `hr.py` (AttendanceHrService), `field_work.py`, `statistics.py`, `reminders.py` | absences, employees, offices, qr_codes, schedules, notifications, telegram, audit, accounts |
| absences | заявки на отсутствие, справки, баланс | AbsenceType, AbsenceRequest, EmployeeAbsence, AbsenceDocument, AbsenceAction, LeaveBalance | `services.py` (AbsenceService), `types_service.py`, `policy.py`, `application_pdf.py` | attendance, employees, files, notifications, schedules, organizations |
| telegram | привязка, Mini App | TelegramAccount, TelegramLinkInvitation | `services.py` (TelegramLinkService, TelegramMiniAppService), `identity.py`, `auth.py`, `tokens.py`, `initdata.py` | audit, employees, notifications, onboarding, schedules |
| notifications | очередь, попытки, лента HR | Notification, NotificationAttempt, FeedRead | `outbox.py`, `service.py`, `feed.py`, `messages.py`, `isolation.py` | audit, realtime, telegram, employees, attendance, absences, questions, onboarding, reports |
| questions | обращения к HR | EmployeeQuestion, QuestionMessage | `inbox.py` (InboxService), `service.py`, `context.py` | employees, notifications, realtime, ai_assistant, knowledge, files, telegram, absences, attendance, schedules |
| knowledge | база знаний и FAQ | KnowledgeSource, KnowledgeChunk, FaqEntry, KnowledgeIndexJob | `knowledge/service.py` | ai_assistant |
| ai_assistant | RAG, эскалация, метрики | UnansweredQuestion, LlmQueryLog, AnswerFeedback | `services/*`, `use_cases/bot.py`, `use_cases/crm.py`, `worker.py`, `providers/*` | knowledge |
| surveys | опросы | SurveyTemplate, SurveyQuestion, SurveyCampaign, SurveyRecipient, SurveyAutomation, SurveyAnswer | `services.py`, `automations.py` | employees, notifications, reports, telegram, departments, offices, positions |
| onboarding | ознакомление и обязательные документы | OnboardingProgram, OnboardingSection, EmployeeOnboarding, EmployeeSectionAcknowledgement, PolicyCategory, PolicyDocument, PolicyDocumentVersion, EmployeePolicyAcceptance | `services.py`, `flow.py`, `progress.py`, `reminders.py`, `seeding.py` | employees, notifications, telegram, audit, surveys |
| reports | выгрузки | ExportJob, ReportTemplate | `service.py`, `worker.py`, `builder.py`, `sheets.py`, `catalog.py`, `export.py`, `storage.py`, `templates.py` | absences, analytics, attendance, employees, departments, offices, schedules, telegram |
| analytics | показатели | — | `metrics.py`, `overview.py`, `dashboard.py`, `people.py`, `movement.py` | absences, attendance, employees, schedules, offices, departments, positions |
| audit | журнал | AuditLog | `core/rbac.py:AuditTrail`, `audit/events.py`, `audit/redaction.py` | — |
| realtime | SSE и LISTEN/NOTIFY | — | `broker.py`, `signals.py`, `views.py` | questions, accounts |
| selfservice | личный кабинет | — | view-модули (`views.py`, `absences.py`, `ask.py`, `questions.py`, `surveys.py`, `notifications.py`) | attendance, absences, questions, onboarding, telegram, surveys |

Примечания по «что создаёт и меняет»:
- `attendance/services.py:165 register_scan` создаёт AttendanceEvent и открывает или закрывает AttendanceSession; может отменять заявки (`:298-304`) и ставить Notification.
- `attendance/hr.py:672 review_correction` меняет статус заявки и применяет `_apply_correction` (`:1308`, сессия → `CORRECTED`, `:1329`); `:783 manual_event` создаёт событие `source=MANUAL`.
- `absences/services.py:_approve` (`:1726`) создаёт EmployeeAbsence (`PLANNED` или `ACTIVE`, `:1869`) и меняет баланс (`_reserve`, `_release`, `_consume_reservation`).
- `employees/services.py:933 terminate` закрывает назначения и графики датой увольнения.
- `telegram/services.py` создаёт и меняет TelegramAccount и TelegramLinkInvitation.
- `notifications/outbox.py:129 enqueue` — единственная точка постановки сообщений.

---

## 3. Карта API

Корни: `config/urls.py` (`/admin/`, `/api/v1/`, `/healthz`, `/readyz`, `/api/schema`; `/api/docs` и `/api/redoc` только при DEBUG). Все остальные пути — под `/api/v1/` (`config/api_urls.py`). Роутер — `DefaultRouter()` (`api_urls.py:126`), `trailing_slash` не переопределён: пути роутера ЗАКАНЧИВАЮТСЯ на `/` (CRM их так и вызывает, `apps/hr-crm/src/api/crm.ts`). Явные `path()` — без завершающего слэша. Идентификаторы в роутере — UUID по `lookup_value_regex` (`core/api.py:50`).

Правило прав: проверки идут в сервисах (`core/api.py:7-9`). Колонка «право» собрана по строкам `access.require*` в сервисе (отмечено «по строкам», если метод сопоставлен по номерам строк). Все CRM-view имеют `IsAuthenticated` (сессия).

### 3.1 Вход и профиль CRM
| Метод и путь | View (файл:строка) | Сервис | Право |
|---|---|---|---|
| POST `/auth/login` | LoginView `accounts/views.py:119` (AllowAny) | `accounts/security.py`, `LOGIN_THROTTLE` | — (лимиты 5/20/30) |
| POST `/auth/logout` | LogoutView `:200` | — | вход выполнен |
| GET `/auth/me` | CurrentUserView `:217` | — | вход выполнен |

### 3.2 Справочники организации
| Метод и путь | View | Сервис и метод | Право |
|---|---|---|---|
| GET/POST `/regions/`, GET/PATCH `/regions/{id}/`, POST `.../deactivate/`, `.../reactivate/` | RegionViewSet `regions/views.py:21` | `regions/services.py` (50,70,86,118,148) | read: `regions.read`; запись: `regions.manage` + `require_region` |
| GET/POST `/offices/`, GET/PATCH `/offices/{id}/`, POST `deactivate/`, `reactivate/`, `location/confirm/`, `location/discard/`, `close/` | OfficeViewSet `offices/views.py:17` | `offices/services.py` (233,258,298,341,446,494…) | read: `offices.read`; запись: `offices.manage` + `require_office/region` |
| `/departments/` (CRUD, DELETE, `deactivate/`, `reactivate/`) | DepartmentViewSet `departments/views.py:102` | `departments/services.py` (120-351) | read `offices.read`; запись `departments.manage`; общий отдел (без офиса) — `require_whole_organization` (`:197`) |
| `/positions/` (CRUD, DELETE, `deactivate/`, `reactivate/`) | PositionViewSet `:150` | `departments/services.py:459-546` | `positions.manage` только на всю организацию |
| `/absence-types/` (CRUD, DELETE, `deactivate/`, `reactivate/`) | AbsenceTypeViewSet `absences/type_views.py:65` | `absences/types_service.py` (82,101,112-203) | read `absences.read`; запись `absences.manage_types` на всю организацию |
| `/work-schedules/` (CRUD, DELETE, `deactivate/`, `reactivate/`, `assign/`, `assign-department/`) | WorkScheduleViewSet `schedules/views.py:23` | `schedules/services.py` (84,96,127-399,462,489) | read `schedules.read`; запись `schedules.manage` (массовое назначение отделу — `scope_for(...,"schedules.manage")`, `:489`) |
| GET `/employees/{id}/schedules` | EmployeeScheduleViewSet `schedules/views.py:105` | `schedules/services.py` | `schedules.read` |
| `/calendar-exceptions/` (CRUD, DELETE, `deactivate/`, `reactivate/`) | CalendarExceptionViewSet `calendar_views.py:115` | `schedules/calendar.py` (93,144,166-377,453) | read `schedules.read`; запись `calendar.manage`; для офиса `require_office` |
| `/qr-points/` (CRUD, DELETE, `activate/`, `deactivate/`, GET `sticker/`, `reissue-token/`) | QrPointViewSet `qr_codes/views.py:246` | `qr_codes/points.py` (146-441) | read `qr_points.read`; запись `qr_points.manage` + `require_office` |
| GET/POST `/qr/devices`, POST `/qr/devices/{id}/{action}` | QrDeviceListView `:150`, QrDeviceActionView `:195` | `qr_codes/services.py` (110-120,221,411,423) | `qr_points.read` / `qr_points.manage` (+ `require_office`) |

### 3.3 Сотрудники
| Метод и путь | View | Метод сервиса | Право |
|---|---|---|---|
| GET `/employees/`, `counts/`, `highlights/`, `search/`, `{id}/`, `{id}/assignments/` | EmployeeViewSet `employees/views.py:63` | `EmployeeService.list/counts/highlights/search/get/assignment_history` | `employees.read` + область (`_office_scope_condition`, `services.py:998`) |
| POST `/employees/` | `create` | `create` (`:614`) | `employees.manage` |
| POST `/employees/onboard/` | `onboard` | `EmployeeOnboardingService.onboard` (`employees/onboarding.py:106`) | `employees.manage` (`:137`); вложенная выдача ссылки Telegram при `telegram.manage` (`:559`) |
| PATCH `/employees/{id}/` | `partial_update` | `update` (`:706`) | `employees.manage` |
| POST `/employees/{id}/change-assignment/` | `change_assignment` | `change_assignment` (`:774`) | `employees.manage` + `require_office(...,"employees.manage")` |
| POST `/employees/{id}/deactivate/` и `/reactivate/` | `deactivate`, `reactivate` | `_set_status` (SUSPENDED и ACTIVE, `:904`) | `employees.manage` |
| POST `/employees/{id}/terminate/` | `terminate` | `terminate` (`:933`) | `employees.archive` |
| POST `/employees/attachments/` | `attachments` | `EmployeeAttachmentService.upload` | `employees.manage` |
| POST `/employees/{id}/documents/`, DELETE `.../documents/{doc}/` | `document_attach/detach` | `attachments.py:113,177` | `employees.manage` |
| GET `/employees/{id}/photo/` | `photo` | `open_photo` (`attachments.py:241`) | `employees.read` |
| POST `/employees/{id}/photo-set/` | `photo_set` | `set_photo` | `employees.manage` |
| GET `/employees/{id}/documents/{doc}/download/` | `document_download` | `open_document` (`attachments.py:258`) | `employees.manage` (`:271`, по строкам) |
| GET `/employees/{id}/telegram` | EmployeeTelegramView `telegram/views.py:232` | `TelegramLinkService.status` | `telegram.read` |
| POST `/employees/{id}/telegram/disconnect` | `:253` | `disconnect` (`services.py:534`) | `telegram.manage` |
| GET `/employees/{id}/onboarding`; POST `/employees/{id}/onboarding/{action}` (`invite, reinvite, revoke, remind, enrol, due`) | `onboarding/views.py:247,273` | `OnboardingService` (`services.py:448-682`) | `onboarding.read`; действия `onboarding.manage`; invite/reinvite/revoke — ещё `telegram.manage` |
| GET/POST `/telegram/invitations/`, `pending/`, POST `{id}/confirm/`, `reject/`, `revoke/` | TelegramInvitationViewSet `telegram/views.py:60` (throttle `telegram_invitations`) | `list_invitations`, `pending`, `create_invitation`, `confirm`, `reject`, `revoke_invitation` | чтение `telegram.read`; остальное `telegram.manage` |

### 3.4 Отметки и выезды (CRM)
| Метод и путь | View | Метод | Право |
|---|---|---|---|
| GET `/attendance/events`, `/attendance/sessions` | AttendanceViewSet `attendance/views.py:261` | `AttendanceHrService.events/sessions` (`hr.py:507,552`) | `attendance.read` + область |
| GET `/attendance/presence` | PresenceView `:365` | `presence` (`hr.py:236`) | `attendance.read` |
| GET `/attendance/daily` | EmployeeDailyView `:602` | `daily` (`:336`) | `attendance.read` |
| GET `/attendance/corrections` | CorrectionListView `:428` | `corrections` (`:638`) | `attendance.read` |
| POST `/attendance/corrections/{id}/{decision}` | CorrectionDecisionView `:468` | `review_correction` (`:672`) | `attendance.correct` |
| POST `/attendance/manual` | ManualEventView `:505` | `manual_event` (`:783`) | `attendance.manual` + `require_office(...,"attendance.manual")` + запрет самому себе (`:762`) |
| GET/POST `/attendance/field-work`; POST `/attendance/field-work/{id}/{action}` (`cancel, retry`) | FieldWorkListView `:89`, FieldWorkActionView `:131` | `FieldWorkService.list/create/cancel/retry` (`field_work.py:178-264`) | чтение `attendance.read`; остальное `attendance.correct` |
| POST `/telegram/bot/field-work/{id}/decision` | BotFieldWorkDecisionView `:153` (`IsTelegramBot`, `authentication_classes=[]`) | `FieldWorkService.decide` (`field_work.py:285`) | только секрет бота; личность — `telegram_user_id` из тела |

### 3.5 Заявки на отсутствие (CRM)
| Метод и путь | View | Метод | Право |
|---|---|---|---|
| GET `/absence-requests/pending` | `absences/views.py:313` | `AbsenceService.pending` (`:1706`) | `absences.read` |
| GET `/absence-requests/{id}` | `:337` | `for_hr` (`:1672`) | `absences.read` |
| GET `/absence-requests/{id}/application` | `:579` | `hr_application` (`:1638`) | `absences.read` |
| POST `/absence-requests/{id}/period` | `:427` | `set_period` (`:1112`) | `absences.approve` |
| POST `/absence-requests/{id}/application-received` | `:462` | `mark_application_received` (`:1211`) | `absences.documents` |
| POST `/absence-requests/{id}/{decision}` | AbsenceDecisionView `:362` | `decide` (`:1006`) или `cancel_approved` (`:1262`) | `absences.approve` + запрет самосогласования (`_forbid_self_approval`, `:2505`) |
| GET `/absence-requests/{id}/documents/{doc}/download` | `:498` | `open_document` (`:1476`) | по `has()` по кодам (`:1491`), не проверено детально |
| POST `/absence-requests/{id}/documents/{doc}/{decision}` | `:537` | `verify_document` (`:1523`) | `absences.documents` |

Маршрут `<str:decision>` стоит ПОСЛЕ именованных (`api_urls.py:430-458`) — порядок критичен.

### 3.6 Очереди, лента, события, аналитика, отчёты
| Метод и путь | View | Сервис | Право |
|---|---|---|---|
| GET `/dashboard` | DashboardView `analytics/views.py:85` | `DashboardService.summary` (`dashboard.py:69`) | `attendance.read` (`:80`), карточки отсутствий — `has("absences.read")` (`:280`) |
| GET `/requests`, `/requests/counts` | `core/queue_views.py:93,213` | собирает очередь заявок и корректировок (проверки внутри вызываемых сервисов, не прослежено) | — |
| GET `/notification-feed`, `/notification-feed/counts`, POST `/notification-feed/read-all`, GET и POST `/notification-feed/{event_id}` | `notifications/feed_views.py:113-221` | `notifications/feed.py` | по виду события: `has("attendance.read"/"questions.read"/"notifications.read"/"onboarding.read"/"employees.read"/"absences.read")` (`feed.py:602-1236`) |
| GET `/analytics`, `/analytics/compare`, `/analytics/movement`, `/analytics/overview`, `/analytics/team` | `analytics/views.py` | `AnalyticsService.organization/region/office/employee/compare` (`metrics.py:194-286`), `OverviewService.overview`, `MovementService.report`, `PeopleService.team` | `analytics.read` (+`employees.read` для сотрудника, `metrics.py:250`); `movement` — `employees.read` (`movement.py:76`) |
| GET `/events/stream` (SSE) | `question_events` `realtime/views.py:150` | `_still_signed_in`, `_sees_question` | `questions.read` (`:107`); обслуживает только ASGI |
| GET `/reports/catalog`, POST `/reports/preview` | `reports/builder_views.py:111,190` | `reports/builder.py` | `reports.export` (`builder.py:1036`) |
| GET `/reports/{kind}/export` | ExportView `reports/views.py:57` | `reports/sheets.py` (`reports/views.py:101`) | `reports.export`; сервисы вызываются с `actor.confined("reports.export")` |
| `/export-jobs/` (GET, POST, GET `{id}/`, `counts/`, POST `cancel/`, `retry/`, `hide/`, GET `download/`) | ExportJobViewSet `reports/job_views.py:186` | `ExportJobService` (`service.py:65-371`) | `reports.export`; скачивание — владелец, либо `reports.download_any` и `scope_for(...,"reports.download_any")` (`service.py:403-453`) |
| `/report-templates/` (GET, POST, DELETE) | `builder_views.py:238` | `reports/templates.py` | `reports.export` (личные шаблоны) |
| GET `/audit-logs` | AuditLogView `audit/views.py:89` | — | `audit.read` (`:149`); область — `scope_for(...,"audit.read")` (`:166`) |
| GET `/onboarding/progress`, `/onboarding/counts`, `/onboarding/export` | `onboarding/views.py:168-224` | `list_progress`, `counts`, `export_rows` | `onboarding.read` |
| POST `/onboarding/remind` | `:335` | `remind_many` (`services.py:673`) | `onboarding.manage` |
| `/onboarding/sections/` (GET, POST, PATCH, POST `archive/`) | `onboarding/views.py:390` | `OnboardingContentService` | чтение `onboarding.read`; запись `onboarding.manage` на всю организацию |
| `/onboarding/documents/` (GET, POST, PATCH, `archive/`, POST `versions/`, GET `pending/`), `/onboarding/categories/` | `:439`, `:352` | `PolicyService`, `CategoryService` | чтение `onboarding.read`; запись `policies.publish` на всю организацию |
| PATCH `/onboarding/versions/{id}`, POST `.../publish`, POST и GET `.../file` | `:507,527,550` | `update_version`, `publish_version` (`services.py:1139`) | `policies.publish`; file: проверка во view (`views.py:573` для записи, `:609` для чтения `onboarding.read`) |

### 3.7 Управление доступом, настройки, база знаний, вопросы, опросы, уведомления
| Метод и путь | View | Сервис | Право |
|---|---|---|---|
| `/users/` (GET, `counts/`, POST, GET и PATCH `{id}/`, POST `set-password/`, `activate/`, `deactivate/`) | CrmUserViewSet `accounts/rbac_views.py:283` | `UserAdminService` (`rbac_service.py:75-425`) | `users.manage` |
| GET и POST `/roles`, GET и PATCH `/roles/{id}`, GET `/permissions` | `rbac_views.py:443-542` | `RoleAdminService` | `roles.manage` (чтение — видимость зависит от `roles.manage`, `:134,159`) |
| GET `/users/{id}/grants`, GET `/grants/scopes`, POST `/grants`, PATCH и DELETE `/grants/{id}` | `rbac_views.py:554-703` | `assign`, `revoke`, `set_validity`, `assignable_scopes` (`:819-1007`) | `roles.manage` + двойная проверка прав и территории + защита последнего SUPER_ADMIN |
| GET `/settings`, GET и PATCH `/settings/{key}`, GET `/settings/integrations` | `organizations/views.py:127-199` | `settings_service.py` | `settings.manage`; запись — на всю организацию (`:173`) |
| `/knowledge/sources/` (GET, `counts/`, `capability/`, `{id}/versions/`, POST, PATCH, POST `{id}/index/`, GET `index-status/`, POST `publish/`, `archive/`) | KnowledgeSourceViewSet `knowledge/views.py:184` | `knowledge/service.py`, `ai_assistant/use_cases/crm.py` | `knowledge.read/write/publish/index` |
| `/knowledge/faq/` (GET, `counts/`, POST, PATCH, POST `activate/`, `archive/`), `/knowledge/index-jobs/` | `:458`, `:610` | то же | `knowledge.read/write/publish` |
| `/knowledge/unanswered-questions/` (GET, POST `assign/`, `close/`, `make-faq/`) | UnansweredQuestionViewSet `questions/views.py:161` | `ai_assistant/services/escalation.py` | `questions.read/answer`, `knowledge.write` (по `crm.py`) |
| `/knowledge/escalations/` (GET, `counts/`, `assignees/`, `{id}/`, `context/`, POST `read/`, `take/`, `assign/`, `priority/`, `category/`, `start/`, `wait/`, `close/`, `reopen/`, `reply/`, `draft/`, GET `messages/{id}/file`) | EscalationViewSet `:595` | `InboxService` (`questions/inbox.py`) | `questions.read` для чтения, `questions.answer` для остальных |
| `/surveys/templates/` (CRUD, DELETE, POST `copy/`, `publish/`, `new-version/`, `archive/`) | SurveyTemplateViewSet `surveys/views.py:270` | `surveys/services.py` | чтение `surveys.read`; запись `surveys.manage` |
| `/surveys/campaigns/` (GET, POST, PATCH, POST `preview/`, `send/`, `cancel/`, `remind/`, GET `recipients/`, `answers/`, `summary/`, `export/`) | SurveyCampaignViewSet `:440` | то же | `surveys.read/manage`; `all_offices` для `surveys.manage` (`services.py:106`) |
| `/surveys/automations/` (CRUD, DELETE, POST `enable/`, `disable/`, GET `history/`) | `:387` | `surveys/automations.py` | `surveys.read/manage` |
| `/notifications/` (GET, `counts/`, GET `{id}/`, `{id}/attempts/`, POST `retry/`, `cancel/`, `retry-failed/`) | NotificationViewSet `notifications/views.py:189` | `NotificationService` (`service.py:116-271`) | чтение `notifications.read`; `retry/cancel/retry-failed` — `notifications.manage` |

### 3.8 Самообслуживание `/me/*` (аутентификация Mini App или бот, `IsLinkedEmployee`, троттл `employee_self` 120/мин, скан 20/мин)
Файл маршрутов `selfservice/urls.py`. Право: проверенная привязка Telegram; employee_id и organization_id из запроса не читаются.

| Метод и путь | View | Логика |
|---|---|---|
| GET `/me/profile` | `selfservice/views.py:85` | данные из `EmployeeContext` |
| GET `/me/status` | `:281` | `attendance/statistics.py:277 current_status` |
| GET `/me/statistics` | `:294` | `statistics.for_period/for_today/for_week/for_month` |
| GET `/me/history` | `:323` | то же, окно до 62 дней |
| POST `/me/attendance/scan` | `:161` | `attendance/scanning.py:140 scan` → `services.register_scan` |
| POST `/me/attendance/notice` | `:250` | `attendance/reminders.py:192 notice` (DayNotice) |
| POST `/me/ask`, `/me/ask/escalate` | `selfservice/ask.py` | `ai_assistant/use_cases/bot.py`, `InboxService.receive` |
| GET и POST `/me/absences`, `/me/absences/options`, GET и DELETE `/me/absences/{id}`, POST `.../extend`, GET `.../application`, POST `.../papers/{paper}`, POST и GET `.../document`, GET `/me/leave-balance` | `selfservice/absences.py` | `AbsenceService.create/extend/cancel/attach_document/own_document/application/send_paper/balances` |
| GET `/me/surveys`, GET и POST `/me/surveys/{recipient_id}` | `selfservice/surveys.py` | `surveys/services.py` (старт и ответ) |
| POST `/me/questions/messages`, GET `/me/questions/replies/{id}/file` | `selfservice/questions.py` | `InboxService.receive`, файл ответа |
| GET `/me/onboarding`, POST `.../start`, GET `.../sections/{n}`, POST `.../acknowledge`, `.../decision`, GET `/me/policies/{id}`, `/me/policies/{id}/file` | `onboarding/self_views.py` | `onboarding/flow.py` (`OnboardingFlow`) |
| GET `/me/notifications`, POST `/me/notifications/{id}/read` | `selfservice/notifications.py` | отметка `READ` (`:139`) |

### 3.9 Bot-пути, Mini App, экраны QR
| Метод и путь | View | Аутентификация |
|---|---|---|
| POST `/telegram/bot/link` | BotLinkView `telegram/views.py:309` | `IsTelegramBot`, троттл `telegram_bot_link`; `TelegramLinkService.consume` (`:595`) |
| POST `/telegram/bot/link/accept` | `:350` | `IsTelegramBot`; `accept_terms` (`:800`) |
| POST `/telegram/bot/recognize` | `:364` | `IsTelegramBot`; `recognize` (`:655`) |
| GET и POST `/telegram/bot/outbox` | BotOutboxView `:475` | `IsTelegramBot`; GET забирает пачку (до 200 результатов в POST) |
| GET `/telegram/bot/outbox/wait` | `realtime/views.py:270 outbox_wait` | секрет бота (`_bot_allowed`, `:260`), только ASGI |
| POST `/telegram/mini-app/auth` | MiniAppAuthView `:413` | AllowAny + троттл `telegram_mini_app` 30/мин; `authenticate` (`:926`) |
| GET `/telegram/mini-app/me` | MiniAppMeView `:439` | Bearer Mini App |
| POST `/qr-display/pair`, GET `/qr-display/code` | `qr_codes/views.py:60,96` | без DRF-аутентификации; одноразовый код и credential экрана; троттлы `qr_display_pair` 10/мин и `qr_display_code` 60/мин |

Служебные: GET `/healthz`, `/readyz` (`core/views.py`); шлюз отдаёт `/gateway-health`, `/health/live`, `/health/ready`.

---

## 4. Модель данных

### 4.1 Базовые классы (`core/models.py`)
- `UUIDPrimaryKeyModel`, `CreatedAtModel`, `TimestampedModel` (+`updated_at`), `ArchivableModel` (`archived_at`), `OrganizationScopedModel` (FK `organization_id`).
- Почти все FK — `on_delete=PROTECT`, `db_index=False` (индексы объявлены явно с именами `ix_*`).
- Статусы — VARCHAR + CHECK через `core/enums.py:283 status_check` (имена `ck_*`), значения собраны в `core/enums.py`.
- Расширения Postgres: `btree_gist`, `pgvector` и др. (`core/migrations/0001_extensions.py`), сырые объекты — `core/migrations/0002_raw_schema_objects.py` (FTS-индексы знаний).

### 4.2 Ключевые сущности и связи
- **Organization** (`organizations/models.py:16`) → Region → Office (`offices/models.py:18`; поля геозоны `geofence_kind` CIRCLE/POLYGON, `geofence_polygon`, `pending_location`, `timezone`) → OfficeNetwork (CIDR), Department (office необязателен), Position (общая).
- **Employee** (`employees/models.py:46`): `employee_number`, `pinfl`, `hire_date`, `termination_date`, `employment_status`, `telegram_connected` (денормализованный флаг).
- **EmployeeAssignment** (`:143`): офис, отдел, должность, руководитель, `employment_type`, `work_mode`, `is_primary`, `valid_from`/`valid_to`. История переводов = цепочка строк.
- **EmployeeOfficeAccess** (`:249`), **EmployeeDocument** (`:320`, `uq_employee_documents_kind`), **EmployeeOnboardingKey** (`:390`).
- **User** (`accounts/models.py:89`, логин в колонке `email`, `uq_users_org_lower_email`, `uq_users_employee_id`) ↔ **UserRoleScope** (`:224`: `role`, `region` или `office` (не оба), `valid_from`, `valid_to`) ↔ **Role / Permission / RolePermission** (`rbac/models.py`).
- **WorkSchedule** (`schedules/models.py:25`: тайм-зона, `weekly_minutes`, допуски) → ScheduleDay (ISO-weekday 1..7, `crosses_midnight`) → ScheduleBreak; **EmployeeScheduleAssignment** (`:132`, период); **CalendarException** (`:201`, `office` необязателен, `is_active`).
- **AttendanceEvent** (`attendance/models.py:28`, неизменяемый, без `updated_at`), **AttendanceSession** (`:183`), **FieldWorkRequest** (`:274`), **AttendanceCorrectionRequest** (`:309`), **DayNotice** (`:384`).
- **AbsenceType**, **AbsenceRequest** (`absences/models.py:70`, `request_kind` CREATE/EXTEND/CANCEL, `parent_request`), **EmployeeAbsence** (`:195`, подтверждённый период), **AbsenceDocument** (`:290`), **AbsenceAction** (`:350`, журнал действий), **LeaveBalance** (`:404`, минуты, ключ сотрудник+тип+год).
- **EmployeeQuestion** (`questions/models.py:38`, `number` уникален в организации) ↔ **QuestionMessage** (`:173`).
- **SurveyTemplate → SurveyQuestion**, **SurveyCampaign** (`is_anonymous`) → **SurveyRecipient** → **SurveyAnswer**, **SurveyAutomation**.
- **OnboardingProgram/Section**, **EmployeeOnboarding**, **EmployeeSectionAcknowledgement**, **PolicyCategory/Document/Version**, **EmployeePolicyAcceptance** (`onboarding/models.py`).
- **TelegramAccount** (`telegram/models.py:37`, одна строка на сотрудника) и **TelegramLinkInvitation** (`:95`, хранится только хеш токена).
- **Notification** (`notifications/models.py:34`, очередь: `attempts`, `next_attempt_at`, `locked_at`, `idempotency_key`) ↔ **NotificationAttempt** (`:124`) ↔ **FeedRead** (`:193`).
- **ExportJob** (`reports/models.py:32`), **ReportTemplate** (`:128`), **File** (`files/models.py:21`: `storage_key`, `checksum_sha256`, `scan_status`, `deleted_at`).
- **AuditLog** (`audit/models.py:20`: `action`, `entity_type`, `entity_id`, `old_values`, `new_values`, `ip_address`, `occurred_at`).
- **OfficeQrPoint**, **QrDisplayDevice**, **QrDisplaySession** (`qr_codes/models.py`), **EmployeeDevice** (`devices/models.py:15`).
- **KnowledgeSource / KnowledgeChunk / FaqEntry / KnowledgeIndexJob**, **UnansweredQuestion / LlmQueryLog / AnswerFeedback**.

### 4.3 Ограничения важные для бизнес-логики (из моделей и миграций)
| Ограничение | Где | Смысл |
|---|---|---|
| `ex_employee_assignments_primary_overlap` (EXCLUDE gist, `is_primary`) | `employees/models.py:225` | нет пересекающихся основных назначений |
| `ex_employee_schedule_assignments_overlap` | `schedules/models.py:173` | нет пересекающихся графиков |
| `ex_employee_absences_overlap` (daterange `[start_date, end_date+1)`, статусы PLANNED/ACTIVE/COMPLETED) | `absences/models.py:265` | нет двух живых отсутствий на одни сутки |
| триггер `uq_absence_requests_open_sick_leave`, функции `absences_lock_employee`, `absences_one_open_sick_leave` | `absences/migrations/0006_calendar_periods.py:73-125` | один открытый больничный |
| `uq_attendance_sessions_one_open` (partial, `status='OPEN'`) | `attendance/models.py:250` | одна открытая сессия на сотрудника |
| `uq_attendance_events_nonce` (partial, ACCEPTED) и `uq_attendance_events_client_event` | `:145,151` | одноразовость QR и идемпотентность повтора |
| `ck_attendance_events_qr_requires_point` | `:129` | отметка по QR обязана иметь точку |
| `ck_attendance_sessions_closed_has_exit`, `..._end_after_start` | `:239-248` | целостность сессий |
| `uq_field_work_active_day` (partial: PENDING/CONFIRMED) | `:301` | один живой выезд на день |
| `uq_attendance_day_notices_employee_day` | `:431` | одно предупреждение на день |
| `uq_telegram_accounts_employee`, `uq_telegram_accounts_tg_user` | `telegram/models.py:75,78` | 1:1 сотрудник и Telegram |
| `uq_telegram_link_invitations_open_employee` (partial, ACTIVE/PENDING_CONFIRMATION) | `:191` | одна живая ссылка |
| `uq_notifications_idempotency_key`, `ck_notifications_running_is_locked` | `notifications/models.py:86-94` | повтор события не дублирует, RUNNING требует `locked_at` |
| `uq_user_role_scopes_grant` (user, role, region, office, valid_from) | `accounts/models.py:272` | в коде признано, что повторную выдачу БД не ловит (`rbac_service.py:25-28`); `ck_user_role_scopes_scope_not_both`, `ck_user_role_scopes_valid_period` |
| `uq_employees_org_number`, `uq_employees_org_pinfl` (partial) | `employees/models.py:103,112` | уникальность в организации |
| `ck_offices_polygon_present`, `ck_offices_geofence_*`, `ck_offices_close_after_open` | `offices/models.py:88-107` | геозона |
| `uq_calendar_exceptions_office_date` и `_org_date` (partial, `is_active`) | `schedules/models.py:255,260` | одно действующее исключение на дату |
| `uq_survey_campaigns_occasion`, `uq_survey_recipients_campaign_employee`, `uq_survey_answers_recipient_question` | `surveys/models.py:262,329,485` | идемпотентность рассылки |
| `uq_policy_versions_published`, `uq_policy_acceptances_employee_version`, `uq_employee_onboarding_employee` | `onboarding/models.py:484,550,237` | одна опубликованная редакция, одно решение |
| `uq_export_jobs_client_request` | `reports/models.py:97` | защита от двойного клика |
| `uq_leave_balances_year`, `ck_leave_balances_*_non_negative` | `absences/models.py:433-445` | баланс на год |
| `ck_files_scan_status`, `ck_files_checksum_length` | `files/models.py:61-63` | |

### 4.4 Где хранится история
- Периоды `valid_from`/`valid_to`: EmployeeAssignment, EmployeeScheduleAssignment, UserRoleScope (`valid_from` — datetime), EmployeeOfficeAccess.
- Журналы действий: `audit_logs` (общий), `absence_actions` (по заявке), `notification_attempts` (по уведомлению), `question_messages` с `kind=SYSTEM` и `event`, `attendance_events` (неизменяемые).
- Снимки: `AttendanceEvent.office_id` фиксируется в момент события (комментарий `models.py:38`); `Notification.attempt_history_complete`; `SurveyCampaign` — снимок аудитории (миграция `surveys/0005_audience_snapshot`).
- Отзыв вместо удаления: роль — `valid_to` в прошлом; календарь — `is_active=false`; сотрудник не удаляется физически.

---

## 5. Статусные машины (значения — `core/enums.py`, переходы — функции)

| Поле | Значения | Переходы выполняют |
|---|---|---|
| Employee.employment_status | ACTIVE, SUSPENDED, TERMINATED, ARCHIVED | `employees/services.py:895 deactivate` (ACTIVE→SUSPENDED), `:901 reactivate`, `:933 terminate` (→TERMINATED). `FINAL_STATUSES=("TERMINATED","ARCHIVED")` (`:81`). Переход в ARCHIVED в коде сервисов не найден (grep) |
| User.status | ACTIVE, INACTIVE, LOCKED, ARCHIVED | `accounts/rbac_service.py:308 set_status`; LOCKED ставит `accounts/security.py` при перебор |
| Telegram account | PENDING, ACTIVE, REVOKED, BLOCKED | `telegram/services.py`: PENDING (`:771`, из `recognize`/`consume`), ACTIVE (`:426` confirm), REVOKED (`:498` reject, `:550` disconnect) |
| Telegram invitation | ACTIVE, PENDING_CONFIRMATION, USED, REJECTED, REVOKED, EXPIRED | `consume`→PENDING_CONFIRMATION (`:776`), `confirm`→USED (`:429`), `reject`→REJECTED (`:486`), `revoke_invitation`→REVOKED (`:386`), ленивое EXPIRED (`:183`, `:641`) |
| AttendanceEvent.verification_status | ACCEPTED, REJECTED, REVIEW | `attendance/services.py:165 register_scan` |
| AttendanceSession.status | OPEN, CLOSED, CORRECTED, INVALID | OPEN/CLOSED в `register_scan` (`:384`), CORRECTED в `hr.py:1329`; INVALID — в сервисах не найден присваивающий код (используется как фильтр, `field_work.py:155`) |
| AttendanceCorrectionRequest.status | DRAFT, SUBMITTED, IN_REVIEW, APPROVED, REJECTED, CANCELLED | `hr.py:727` (APPROVED/REJECTED), `services.py:298` (CANCELLED); создания в боевом коде нет (только `core/demo/*`) |
| FieldWorkRequest.status | PENDING, CONFIRMED, DECLINED, CANCELLED, EXPIRED (литералы в модели, НЕ из `enums.py`) | `field_work.py:51 expire_stale` (EXPIRED), `:244 cancel`, `:285 decide`, `:178 create` |
| DayNotice.kind | LATE, ABSENT | `reminders.py:192 notice` |
| AbsenceRequest.status | DRAFT, SUBMITTED, IN_REVIEW, APPROVED, REJECTED, CANCELLED | `create` (SUBMITTED, `:765`), `extend` (`:904`), `cancel` (`:963`), `decide`→`_approve` (`:1822,1897`)/REJECTED (`:1078`), `cancel_approved` (`:1281`) |
| EmployeeAbsence.status | PLANNED, ACTIVE, COMPLETED, CANCELLED | `_approve` (`:1869`), отмена (`:971,1295,1914`); COMPLETED — в просмотренных строках присваивания не найдено |
| AbsenceDocument.verification_status | PENDING, VERIFIED, REJECTED | `verify_document` (`:1591`) |
| AbsenceAction.action | CREATED … MARKS_OVERRIDDEN (12 значений) | `_act` (`:2486`) |
| Notification.status | PENDING, RUNNING, SENT, FAILED, CANCELLED, READ | `notifications/outbox.py`: `claim`→RUNNING (`:255`), `mark_sent`, `mark_failed`→FAILED/PENDING (`:364,370`), CANCELLED (`:210,236`), `reclaim_stale`; ручной `service.py:retry/cancel`; READ — `selfservice/notifications.py:139` |
| ExportJob.status | QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED | `reports/worker.py:74,124,230,275,279`; `service.py:260,301,354` |
| KnowledgeSource.status | DRAFT, INDEXING, ACTIVE, ARCHIVED, ERROR | `ai_assistant/services/publishing.py:116-223` |
| KnowledgeIndexJob.status | QUEUED, RUNNING, SUCCEEDED, FAILED | `ai_assistant/worker.py:56-84` |
| FaqEntry.status | DRAFT, ACTIVE, ARCHIVED | `knowledge/service.py:524` и др. |
| UnansweredQuestion.status | NEW, IN_REVIEW, ANSWERED, IGNORED | `ai_assistant/services/escalation.py:56-113` |
| EmployeeQuestion.status | NEW, IN_PROGRESS, WAITING_EMPLOYEE, CLOSED | `questions/inbox.py:400,441,532,727,805,882,891,905` |
| SurveyTemplate.status | DRAFT, PUBLISHED, ARCHIVED | `surveys/services.py:318` и др. |
| SurveyCampaign.status | DRAFT, SCHEDULED, ACTIVE, FINISHED, CANCELLED | `services.py:581` (SCHEDULED), `:1186` (ACTIVE), `:1264` (FINISHED), `:890` (CANCELLED) |
| SurveyRecipient.status | PENDING, SENT, STARTED, COMPLETED, SKIPPED, EXPIRED | `services.py:1161,1181,1263,1377,1475` |
| EmployeeOnboarding.status | NOT_STARTED, IN_PROGRESS, INFO_COMPLETED, POLICIES_IN_PROGRESS, COMPLETED, UPDATE_REQUIRED, BLOCKED_BY_DECLINED_POLICY | вычисляется `onboarding/progress.py`, хранится «ради списков» (`enums.py:164-169`) |
| PolicyDocumentVersion.status | DRAFT, PUBLISHED, ARCHIVED | `onboarding/services.py:1167,1176` |
| QrDisplayDevice.status | PENDING, ACTIVE, REVOKED | `qr_codes/services.py:172,201,258` |
| QrDisplaySession.status | ACTIVE, EXPIRED, REVOKED, CLOSED | `qr_codes/services.py:397` (CLOSED) |
| File.scan_status | PENDING, UNSCANNED, CLEAN, INFECTED, FAILED | запись `files/storage.py:195` (UNSCANNED при выключенном сканере) |
| Office, Region, Department, Position, Organization, WorkSchedule | ACTIVE, INACTIVE, (CLOSED), ARCHIVED | соответствующие сервисы (`deactivate`, `reactivate`, `close`) |

---

## 6. Роли и права

### 6.1 Каталог (`core/permissions_catalog.py:18-111`) — 55 кодов
regions.read/manage; offices.read/manage; departments.manage; positions.manage; employees.read/manage/archive/access; telegram.read/manage; onboarding.read/manage; policies.publish; attendance.read/correct/manual; qr_points.read/manage; qr_display.start; schedules.read/manage; calendar.manage; absences.read/approve/manage_types/documents; surveys.read/manage; leave_balances.manage; knowledge.read/write/publish/index; questions.read/answer; notifications.read/manage; analytics.read; reports.export; reports.download_any; users.manage; roles.manage; settings.manage; audit.read; ai.metrics.read.

Источник правды продублирован: каталог, собственные списки `PERMISSIONS` в миграциях `rbac/migrations/0002…0007`, команда `core/management/commands/seed.py`.

### 6.2 Системные роли (`SYSTEM_ROLES`, `ROLE_PERMISSIONS`, `:115-202`)
- **SUPER_ADMIN** — все коды.
- **HR_ADMIN** (`_HR_FULL`, :130) — все кадровые операции, включая `policies.publish`, `reports.download_any`, `audit.read`; БЕЗ `users.manage`, `roles.manage`, `settings.manage`, `qr_display.start`.
- **REGIONAL_HR** — employees.read/manage/access, telegram.*, onboarding.read/manage, attendance.read/correct, qr_points.read, schedules.*, absences.read/approve/documents, knowledge.read, questions.*, analytics.read, reports.export, notifications.read.
- **OFFICE_ADMIN** — read-набор + attendance.correct/manual, qr_points.manage, qr_display.start.
- **MANAGER** — employees.read, attendance.read, absences.read/approve, schedules.read, knowledge.read, analytics.read.
- **ACCOUNTANT** — employees.read, attendance.read, absences.read, schedules.read, analytics.read, reports.export.
- **TECH_ADMIN** — users.manage, roles.manage, settings.manage, audit.read, offices.read, qr_points.read/manage, knowledge.index, ai.metrics.read, notifications.read/manage.
- **VIEWER** — только чтение: employees, offices, attendance, schedules, absences, knowledge, analytics, surveys.read, onboarding.read.
- Выдача через UI ограничена `OFFERED_ROLE_CODES` (`enums.py:279`): SUPER_ADMIN, HR_ADMIN, OFFICE_ADMIN, MANAGER, VIEWER.

### 6.3 Механизм области (`core/rbac.py`)
- `Actor(user_id, organization_id, confine)` (`:34`); `Actor.confined(permission)` сужает любую проверку области до одного права (используется выгрузками, `:51`).
- `Scope(all_offices, office_ids, region_ids)` (`:58`): `None` и пустое множество — разные состояния (`visible_office_ids` возвращает `None` для всей организации, `:384`).
- `AccessControl.permissions(actor)` (`:104`) — объединение прав по всем действующим назначениям (`_active_scopes`, `:134`; учитывает `valid_from/to`, статус пользователя ACTIVE, `archived_at IS NULL`).
- `scope()` (`:179`) — объединённая область; `scope_for(actor, permission)` (`:193`) — область только тех назначений, чья роль несёт это право (закрывает повышение комбинацией назначений).
- `require`, `has` (`:123,129`); `require_whole_organization` (`:248`) — для общих объектов (настройки, типы отсутствий, должности, программа ознакомления, документы); `require_office/region/whole_region/offices_in_region/region_condition` (`:267-363`); `require_schedule` (`:365`, только организация); фильтры `office_filter`, `region_filter`.
- Чужая организация отвечает как «не найдено», свой объект вне области — отказ (`:267-289`).
- Кэш прав живёт в экземпляре `AccessControl`, сбрасывается `invalidate()` (`:240`).
- Защиты в `rbac_service.py`: выдача только прав и территорий выдающего (двойная проверка), последний SUPER_ADMIN защищён блокировкой строки роли (`:1258-1317`), SUPER_ADMIN нельзя завести или править через API (`:1337`), роль отзывается `valid_to`.
- Django Admin: доступ у SUPER_ADMIN с назначением без сужения (`accounts/models.py:177-194`), на шлюзе дополнительно ограничен списком адресов.
- Разделение обязанностей: `_forbid_self_approval` (`absences/services.py:2505`), `_forbid_self_attendance` (`attendance/hr.py:762`).

---

## 7. Аналитика: где считаются показатели (только перечень)

Backend:
- `analytics/metrics.py` — `AnalyticsService.organization/region/office/employee/compare` (`:194-286`), классы `Ratio`, `Coverage`, `DayPoint`, `Report` (`:63-152`).
- `analytics/overview.py:84 OverviewService.overview` (сводка с прошлым периодом, рейтинг офисов, ритм прихода, дни недели).
- `analytics/dashboard.py:69 DashboardService.summary` (карточки главной).
- `analytics/people.py:46 PeopleService.team`; `analytics/movement.py:67 MovementService.report`.
- `attendance/statistics.py` — `for_today/for_week/for_month/for_period` (`:190-205`), `current_status` (`:277`), `DayPlan`, `PeriodReport` (свойства `seconds`, `attended_days`, `working_days`, `missed_days`, `sick_leave_days`, `vacation_days`, `other_absence_days`, `:132-178`). Используется self-service, аналитикой (`metrics.py:40`, `overview.py:44`) и HR (`hr.py:45`).
- `attendance/hr.py` — `presence`, `daily`, `_presence_row` (`:236,336,1180`), `correction_queue` (`:593`), `counts`.
- `employees/services.py` — `highlights`, `counts` (`:162,252`), `roster_assignment_filter`, `WORKING_STATUSES`.
- `notifications/feed.py` — счётчики ленты; `core/queue_views.py` — `RequestCountsView`; `questions/inbox.py` — счётчики очереди обращений; `onboarding/services.py:337 counts` и `list_progress`; `surveys/services.py:913-1000` (сводка, анонимный порог `ANONYMOUS_MIN_RESPONSES`); `notifications/service.py:132 counts`; `reports/service.py:89 counts` (задания); `ai_assistant/services/metrics.py`; `absences/services.py` (`leave_check`, `_working_days`, `hr_period_summary`, `balances`).
- Выгрузки: `reports/sheets.py`, `reports/builder.py`, `reports/catalog.py`, `reports/export.py`.
- Self-service: `selfservice/views.py` (`StatisticsView`, `HistoryView`, `_report`), `selfservice/presentation.py` (`summary_json`, `day_json`, `status_json`), `selfservice/responses.py`.

Клиенты:
- CRM: `apps/hr-crm/src/features/analytics/charts.tsx`, `features/dashboard/data.ts`, `features/employee/attendance-model.ts`, `features/attendance/period.ts`, `features/requests/model.tsx`, `features/shell/badges.ts`, `pages/AnalyticsPage.tsx`, `pages/DashboardPage.tsx`, `pages/AttendancePage.tsx`.
- Mini App: `src/screens/Stats.tsx`, `History.tsx`, `Home.tsx`, `home-model.ts`, `format.ts`.
- Бот: `src/messages/employee.py`, `src/messages/attendance.py` (рендер данных `/me/statistics`).

---

## 8. Тесты

| Набор | Где | Что покрывает |
|---|---|---|
| Django-тесты backend | `apps/backend-api/django_tests/` (около 115 файлов) | Основной набор. Модули ниже |
| pytest-дубли | `apps/backend-api/tests/{unit,integration}/` (около 21 файла) | Те же имена: `test_ai_*`, `test_hr_schedules`, `test_hr_validation`, `test_hr_employees`, `test_hr_org_structure`, `test_qr_attendance`, `test_permission_scopes`, `test_schema_constraints` |
| Бот | `apps/employee-telegram-bot/tests/` (19 файлов) | link_flow, onboarding, ask_hr, absence_document, field_work, day_start, menu, keyboard_states, notifications_sender/wake, sticker_scan, web_app_scan, security_bot, check_config |
| Mini App | `apps/employee-mini-app/tests/` (12 файлов) | auth, contracts, home, shell, native-shell, notes, survey, security, scanner, keyboard-scan, quick-scan, ui |
| CRM | `apps/hr-crm/tests/` (около 45 файлов) | admin, analytics, attendance, dashboard, employees, employee-card/lifecycle, field-work, notifications, notification-feed, onboarding, offices/geofence, questions(+live), reports, requests, security, session, surveys, telegram-confirm и др. |
| qr-display | `apps/qr-display/tests/display.test.ts` | клиент экрана |
| Инфра | `infrastructure/backup/tests/` | скрипты бэкапа |
| e2e и интеграция верхнего уровня | `tests/end-to-end`, `tests/integration`, `tests/performance`, `tests/security` | только `.gitkeep` |

Карта `django_tests` по модулям (по именам):
- Сотрудники: `test_hr_employees`, `test_hr_employee_card`, `test_hr_employee_onboarding`, `test_employee_lifecycle`, `test_employee_auth`.
- Структура: `test_hr_org_structure`, `test_hr_catalogs`, `test_office_setup`, `test_office_geofence_polygon`, `test_hr_qr_points`.
- Графики и календарь: `test_hr_schedules`, `test_hr_calendar`, `test_hr_calendar_activation`.
- Отметки: `test_qr_*`, `test_bot_attendance_scan`, `test_hr_attendance`, `test_statistics`, `test_attendance_reminders`, `test_reminders_heartbeat`, `test_field_work*`.
- Отсутствия: `test_absences`, `test_absence_application`, `test_absence_concurrency`.
- RBAC и безопасность: `test_hr_rbac`, `test_hr_rbac_admin`, `test_permission_scopes`, `test_security_*` (auth, miniapp, bot, rbac_matrix, rbac_scope, rbac_input, scope_regression, files, absences, attendance, reports, outbox, questions, surveys, ai, infra, generic, shared_throttle, secret_rotation, fix_2026_10_11), `test_demo_role_permissions`.
- Аналитика: `test_hr_analytics`, `test_analytics_filters/overview/movement/people`.
- Отчёты: `test_hr_reports`, `test_report_kinds`, `test_report_builder`, `test_export_jobs`, `test_export_heartbeat`.
- Уведомления: `test_notifications*`, `test_notification_feed`.
- Вопросы: `test_questions_inbox`, `test_selfservice_ask`, `test_realtime`.
- Опросы: `test_surveys`, `test_surveys_scenario`, `test_survey_automations`.
- Ознакомление: `test_onboarding`, `test_onboarding_workspace`.
- Telegram: `test_telegram_link`, `test_telegram_initdata`, `test_telegram_concurrency`, `test_api_telegram`.
- Знания и AI: `test_knowledge_*`, `test_ai_*`.
- Схема и общее: `test_schema_constraints`, `test_schema_parity`, `test_orm_contract`, `test_openapi`, `test_health`, `test_bootstrap`, `test_admin*`, `test_hr_audit`, `test_hr_settings`, `test_api_hr`, `test_end_to_end_flow`, `test_seed_*`.

---

## 9. Опасные операции и точки входа

| Операция | Точка входа | Исполнитель | Право и защита |
|---|---|---|---|
| Выдача и отзыв ролей, область | POST `/grants`, PATCH и DELETE `/grants/{id}`, POST `/roles`, PATCH `/roles/{id}` | `rbac_service.py:902,963,1007,612,653` | `roles.manage`, двойная проверка, защита последнего SUPER_ADMIN |
| Учётные записи CRM | POST и PATCH `/users/`, `set-password`, `activate`, `deactivate` | `rbac_service.py:231-425` | `users.manage` |
| Увольнение | POST `/employees/{id}/terminate/` | `employees/services.py:933` | `employees.archive`; закрывает назначения и графики; Telegram и пользователя CRM не затрагивает (см. раздел 11) |
| Приостановка и возврат | `deactivate`, `reactivate` | `employees/services.py:895,901` | `employees.manage` |
| Перевод | POST `/employees/{id}/change-assignment/` | `employees/services.py:774` | `employees.manage` + `require_office` |
| Решения по заявкам | POST `/absence-requests/{id}/{decision}`, `period`, `application-received`, `documents/.../{decision}` | `absences/services.py:1006,1112,1211,1262,1523` | `absences.approve` / `absences.documents`, запрет самосогласования |
| Исправление отметок и ручные отметки | POST `/attendance/corrections/{id}/{decision}`, `/attendance/manual` | `attendance/hr.py:672,783` | `attendance.correct`, `attendance.manual`, запрет «себе» |
| Выезд | POST `/attendance/field-work`, `{id}/cancel|retry`, `/telegram/bot/field-work/{id}/decision` | `attendance/field_work.py` | `attendance.correct` / секрет бота |
| Привязка Telegram | POST `/telegram/invitations/` и `confirm`, `reject`, `revoke`, `/employees/{id}/telegram/disconnect`, `/employees/onboard/`, bot link, link/accept, recognize | `telegram/services.py` | `telegram.manage`; со стороны бота — общий секрет; подтверждение всегда HR |
| Загрузка файлов | POST `/employees/attachments/`, `/me/absences` и `.../document`, POST `/onboarding/versions/{id}/file`, ответы HR с файлом | `files/storage.py` (сигнатуры, размеры), `files/serving.py` | права соответствующих сервисов |
| Выгрузки | GET `/reports/{kind}/export`, POST `/export-jobs/`, GET `{id}/download/` | `reports/*` | `reports.export`, `reports.download_any`, область через `confine` |
| Публикация обязательных документов | POST `/onboarding/versions/{id}/publish` | `onboarding/services.py:1139` | `policies.publish` на всю организацию |
| Опросы | POST `/surveys/campaigns/{id}/send/`, `cancel/`, `remind/`, `automations/*/enable` | `surveys/services.py`, `automations.py` | `surveys.manage` |
| Настройки организации | PATCH `/settings/{key}` | `settings_service.py` | `settings.manage` на всю организацию |
| База знаний | `publish`, `index`, `archive`, `make-faq` | `knowledge/service.py`, `ai_assistant/services/publishing.py` | `knowledge.publish/index/write` |
| QR | `qr-points` `reissue-token`, `sticker`, `qr/devices/*` | `qr_codes/*` | `qr_points.manage` |
| Повтор и отмена уведомлений | POST `/notifications/{id}/retry|cancel`, `retry-failed` | `notifications/service.py` | `notifications.manage` |
| Вход и перебор | POST `/auth/login`, `/telegram/mini-app/auth`, `/qr-display/pair`, `/me/attendance/scan` | `accounts/security.py`, `core/throttling.py` | счётчики в PostgreSQL (`AuthRateCounter`) |
| Шлюз и админка | `/admin/` | Django Admin | только адреса из `GATEWAY_ADMIN_ALLOW` и SUPER_ADMIN без сужения |
| Обслуживание данных (не API) | `restore_deleted_notifications`, `seed_*`, `demo_data`, `purge_exports`, `index_knowledge` | management-команды | вручную; сидеры защищены `notifications/isolation.py:require_isolated_stand` |

---

## 10. Расхождения «документация и код»

1. `core/enums.py:60-61`: «Анонимного вида здесь нет и не появится». В коде: `SurveyCampaign.is_anonymous` (`surveys/models.py:229`), миграция `surveys/0006_anonymous_campaigns`, режим экспорта и сводки `surveys/services.py:816,918,947`.
2. `docs/database/schema.md:1014`: `ck_files_scan_status` без `UNSCANNED`. В коде `FILE_SCAN_STATUSES` содержит `UNSCANNED` (`enums.py:121`), миграция `files/0002_file_scan_unscanned`.
3. `core/enums.py:7-9`: «один список на весь проект». Статусы `FieldWorkRequest` заданы литералами в модели (`attendance/models.py:282,299`), `REPORT_TEMPLATE_KINDS` — в `reports/models.py:124`, `consumed_via` — в `telegram/models.py:151`.
4. `docs/architecture/ai-assistant.md:233,520`: запуск воркера как `src.modules.ai_assistant.worker`. В коде модуль лежит в `humotech/ai_assistant/worker.py`.
5. `config/api_urls.py:428-429`: комментарий «React-интерфейса для них пока нет». CRM вызывает эти маршруты (`apps/hr-crm/src/api/crm.ts:856,885`).
6. `api_urls.py:283-284`: «`outbox/wait` обслуживает ASGI-процесс событий». На внутреннем входе `:8080` — да (`nginx.conf:329`). Через `:80` маршрут уходит в gunicorn (локация `/api/v1/telegram/bot/` без XFF), где `_wsgi_refused` отвечает 503 (`realtime/views.py:63`).
7. `docs/architecture/background-jobs.md` не описывает ни процесс `events`, ни то, что опросы и уборка очереди запускаются из `BotOutboxView.get` (поиск по документу слов `events`/`SSE`/`outbox/wait` — пусто).
8. `infrastructure/docker/docker-compose.yml` содержит только postgres, хотя в `deploy/test-server/docker-compose.yml:~178` на него ссылаются как на «локальный стенд». Реальные службы — в `docker-compose.local.yml`.
9. `docs/audits/HUMOTECH_MASTER_AUDIT_PROGRESS.md:19`: стадия 1 «в работе»; остаток L-3…L-8, L-11, L-13, L-14 (не проверялось в коде).

---

## 11. Наблюдения для следующих этапов (только факты)

Права и области
1. Разрешения `qr_display.start`, `employees.access`, `leave_balances.manage`, `ai.metrics.read` есть в каталоге и ролях (`permissions_catalog.py:30,61,78,110`), но в коде вне каталога и миграций не проверяются (grep по `humotech/` и `hr-crm/src`). `ai.metrics.read` упомянут только в docstring `ai_assistant/use_cases/crm.py:13`; метрики там проверяются через `audit.read` (`crm.py:303`).
2. HR_ADMIN (`_HR_FULL`, `permissions_catalog.py:130-151`) не содержит `qr_display.start`, у OFFICE_ADMIN он есть (`:175`). `OFFERED_ROLE_CODES` (`enums.py:279`) исключает REGIONAL_HR, ACCOUNTANT, TECH_ADMIN.
3. Проверка права в трёх местах не в сервисе: `reports/views.py:101`, `onboarding/views.py:573,609`; остальные — в сервисах (`core/api.py:7-9` декларирует иное).
4. Скачивание документа сотрудника требует `employees.manage` (`employees/attachments.py:271`), а фото — `employees.read` (`:242`).
5. `uq_user_role_scopes_grant` включает nullable `region`/`office` и `valid_from`; дубликаты ловит только код (`accounts/models.py:272`, `rbac_service.py:25-28`).
6. `GET /notification-feed*` и `GET /requests*` имеют только `IsAuthenticated` на view; права `has(...)` распределены по видам внутри `feed.py` и сервисов (`feed.py:602-1236`).

Данные и целостность
7. `audit_logs` неизменяем средствами модели (`audit/models.py:72-91`: `save/delete`). Роль `humotech_app` получает `SELECT, INSERT, UPDATE, DELETE` на ВСЕ таблицы (`deploy/test-server/initdb/sql/02_least_privilege.sql:46`), триггеров запрета UPDATE/DELETE для `audit_logs` в миграциях не найдено.
8. `AttendanceCorrectionRequest` создаётся только в `core/demo/queues.py:311` и `core/demo/hr/requests.py:234`. Путей создания для сотрудника (`/me/*`, бот) нет, но CRM читает и решает такие заявки (`attendance/views.py:428,468`).
9. `LeaveBalance` создаётся только в `core/management/commands/demo_data.py:191,277`; маршрута, задающего `allocated_minutes`, нет. `AbsenceService` меняет только `used/reserved` (`absences/services.py:2322-2408`).
10. `terminate()` (`employees/services.py:933-994`) закрывает назначения и графики, но не меняет `TelegramAccount`, не отключает связанного `User` и не отменяет открытые заявки; доступ бота закрывается проверкой на чтении (`telegram/identity.py:141-146`).
11. Статус сотрудника `ARCHIVED` входит в `FINAL_STATUSES` (`employees/services.py:81`), но кода, который его выставляет, в сервисах не найдено. Статус сессии `INVALID` присваивающим кодом не найден.
12. `ck_employee_assignments_employment_type` по-прежнему допускает `INTERN` (`enums.py:34`, `docs/database/schema.md:467`); миграция `employees/0011` пересобрала только CHECK по `employment_status`.
13. `File`: два предиката «можно отдавать» — `files/storage.py:207-218` (строго CLEAN) и `files/serving.py:158-162` (CLEAN или UNSCANNED). По умолчанию сканер выключен, запись идёт как UNSCANNED (`storage.py:195`).
14. Периоды: `EmployeeAbsence` хранит одновременно моменты (`start_at/end_at`) и даты (`start_date/end_date`); пересечения решает только `daterange` по датам (`absences/models.py:219-229,265`).
15. Миграции `rbac/0002…0007` содержат собственные копии списков прав; каталог и `seed.py` — ещё две копии.

Процессы и инфраструктура
16. Опросы по сроку, напоминания по опросам, автоматизации и `reclaim_stale` стартуют только из `GET /telegram/bot/outbox` (`telegram/views.py:500-532`); исключения логируются и не прерывают выдачу. Остановка бота останавливает и их; heartbeat у этого цикла нет.
17. Backend не отправляет сообщения в Telegram: доставка = опрос бота (`notifications/outbox.py:claim`) + отчёт `POST /telegram/bot/outbox`.
18. `purge_exports`, `index_knowledge` и `ai_assistant/worker.py` не запускаются ни в одном compose-файле; `AI_ASSISTANT_ENABLED="false"` в test-server (`docker-compose.yml:71`).
19. SSE и ожидание очереди: один процесс `events` с одним воркером (`--workers 1`), один `LISTEN` (`realtime/broker.py`); доступ к потоку — `questions.read` (`realtime/views.py:107`). На Caddy общий таймаут записи закрывает поток примерно раз в 150 с (`Caddyfile:62-66`).
20. Gateway `:80`: путь `/api/v1/telegram/bot/*` без XFF проксируется на backend (устаревшие внутренние клиенты, `nginx.conf:113-115`). Комментарий в `nginx.conf` называет это временной совместимостью.
21. Пути бота скрыты от «внешнего» трафика признаком наличия XFF (`nginx.conf:116-127`), т.е. граница держится на том, что внешний прокси всегда его ставит и порт шлюза не публикуется.
22. `BotFieldWorkDecisionView` принимает `telegram_user_id` из тела (`attendance/views.py:164-166`) при общем секрете бота; для `/me/*` Telegram ID приходит заголовком.
23. Роль `humotech_app` не имеет DDL, но миграции `core/0002_raw_schema_objects` и `absences/0006` создают функции и триггеры; их выполняет `humotech_owner` (compose `migrate`).

Клиенты и контракты
24. Mini App не вызывает `/me/ask*`, `/me/questions/*`, `/me/onboarding*`, `/me/policies/*`, `/me/attendance/notice` (grep `employee-mini-app/src/api.ts`); бот не вызывает `/me/surveys*`, `/me/notifications*`, POST `/me/absences` (grep `employee-telegram-bot/src/api/selfservice.py`).
25. `AbsenceApplicationView` существует дважды: CRM (`absences/views.py:579`) и self-service (`selfservice/absences.py:391`); маршруты `<str:decision>` стоят последними (`api_urls.py:448-458`).
26. Роутер с завершающим `/` и явные `path()` без него в одном `api_urls.py`; `APPEND_SLASH` не переопределён, `CommonMiddleware` включён.

Тесты и сопровождение
27. В `apps/backend-api/tests/` и `django_tests/` повторяются имена файлов (`test_hr_schedules`, `test_hr_validation`, `test_hr_employees`, `test_hr_org_structure`, `test_ai_*`, `test_qr_attendance`, `test_permission_scopes`, `test_schema_constraints`); соответствие описано в `docs/architecture/django-test-mapping.md`.
28. Каталог `tests/` верхнего уровня (`end-to-end`, `integration`, `performance`, `security`) пуст (`.gitkeep`), а тесты безопасности лежат в `django_tests/test_security_*`.
29. Крупные файлы с логикой: `absences/services.py` (~2700 строк), `notifications/feed.py` (~1400), `questions/inbox.py` (~1500), `analytics/metrics.py` (~800+), `reports/builder.py` (~1000+), `attendance/hr.py` (~1300), `surveys/services.py` (~1600), `apps/hr-crm/src/api/crm.ts` (~3900 строк).
30. `core/management/commands/` содержит демо- и тестовые сидеры (`seed_demo_hr_data`, `seed_pentest_data`, `e2e_admin`, `seed_demo_crm`) в том же пакете, что и боевой код; защита от запуска на рабочей базе — `notifications/isolation.py:66 require_isolated_stand` (какие команды её вызывают, не проверялось).
