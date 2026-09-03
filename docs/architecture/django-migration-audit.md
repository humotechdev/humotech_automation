# Аудит переноса: SQLAlchemy + Alembic -> Django ORM + Django migrations

Этап 1 перехода на Django. Таблица соответствия построена **из снимка живой**
**схемы**, а не из чтения кода: 44 таблицы, 92 CHECK, 127 FK, 20 UNIQUE,
2 EXCLUDE и 176 индексов вручную не сверить без ошибок.

Эталон — база, развёрнутая миграциями Alembic `0001` и `0002`. Снимок снимается
`scripts/schema_snapshot.py`, им же на этапе 5 сверяется схема, которую построят
Django-миграции.

## Сводка

| Показатель | Значение |
| --- | ---: |
| Бизнес-таблиц | 44 |
| Django-приложений | 19 |
| CHECK-ограничений | 92 |
| FOREIGN KEY | 127 |
| UNIQUE-ограничений | 20 |
| EXCLUDE USING gist | 2 |
| Индексов | 175 |
| Расширений PostgreSQL | 3: btree_gist, plpgsql, vector |

## Таблица соответствия

| SQLAlchemy-модель | Таблица | Django-приложение | Django-модель | CK | FK | UQ | IX | Сервис | Способ переноса |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| `organizations.Organization` | `organizations` | `humotech.organizations` | `Organization` | 1 | 0 | 0 | 2 | — | Index(Lower(...)) |
| `organizations.OrganizationSetting` | `organization_settings` | `humotech.organizations` | `OrganizationSetting` | 0 | 1 | 0 | 3 | — | стандартный makemigrations |
| `regions.Region` | `regions` | `humotech.regions` | `Region` | 1 | 1 | 1 | 3 | regions/service.py RegionService | стандартный makemigrations |
| `offices.Office` | `offices` | `humotech.offices` | `Office` | 3 | 2 | 1 | 4 | offices/service.py OfficeService | стандартный makemigrations |
| `offices.OfficeNetwork` | `office_networks` | `humotech.offices` | `OfficeNetwork` | 0 | 2 | 1 | 4 | offices/service.py OfficeService | **custom field** (cidr) |
| `departments.Department` | `departments` | `humotech.departments` | `Department` | 2 | 3 | 1 | 5 | — | стандартный makemigrations |
| `positions.Position` | `positions` | `humotech.positions` | `Position` | 1 | 1 | 1 | 3 | — | стандартный makemigrations |
| `users.User` | `users` | `humotech.accounts` | `User` | 1 | 2 | 0 | 4 | core/permissions/scopes.py | UniqueConstraint(condition)/Index(condition), Index(Lower(...)) |
| `roles.Role` | `roles` | `humotech.accounts` | `Role` | 0 | 1 | 0 | 4 | core/permissions/scopes.py | UniqueConstraint(condition)/Index(condition) |
| `roles.Permission` | `permissions` | `humotech.accounts` | `Permission` | 0 | 0 | 1 | 2 | core/permissions/{catalog,scopes}.py | стандартный makemigrations |
| `roles.RolePermission` | `role_permissions` | `humotech.accounts` | `RolePermission` | 0 | 2 | 0 | 1 | core/permissions/scopes.py | CompositePrimaryKey |
| `roles.UserRoleScope` | `user_role_scopes` | `humotech.accounts` | `UserRoleScope` | 2 | 5 | 1 | 5 | core/rbac.py AccessControl | стандартный makemigrations |
| `employees.Employee` | `employees` | `humotech.employees` | `Employee` | 2 | 1 | 1 | 4 | employees/service.py EmployeeService | стандартный makemigrations |
| `employees.EmployeeAssignment` | `employee_assignments` | `humotech.employees` | `EmployeeAssignment` | 4 | 6 | 0 | 5 | employees/service.py EmployeeService | ExclusionConstraint |
| `employees.EmployeeOfficeAccess` | `employee_office_access` | `humotech.employees` | `EmployeeOfficeAccess` | 2 | 4 | 1 | 5 | — | стандартный makemigrations |
| `schedules.WorkSchedule` | `work_schedules` | `humotech.schedules` | `WorkSchedule` | 4 | 1 | 0 | 2 | schedules/service.py WorkScheduleService | стандартный makemigrations |
| `schedules.ScheduleDay` | `schedule_days` | `humotech.schedules` | `ScheduleDay` | 2 | 1 | 1 | 3 | schedules/service.py WorkScheduleService | стандартный makemigrations |
| `schedules.ScheduleBreak` | `schedule_breaks` | `humotech.schedules` | `ScheduleBreak` | 0 | 1 | 0 | 2 | schedules/service.py WorkScheduleService | стандартный makemigrations |
| `schedules.EmployeeScheduleAssignment` | `employee_schedule_assignments` | `humotech.schedules` | `EmployeeScheduleAssignment` | 1 | 4 | 0 | 5 | schedules/service.py WorkScheduleService | ExclusionConstraint |
| `schedules.CalendarException` | `calendar_exceptions` | `humotech.schedules` | `CalendarException` | 1 | 2 | 0 | 5 | — | UniqueConstraint(condition)/Index(condition) |
| `qr_codes.OfficeQrPoint` | `office_qr_points` | `humotech.qr_codes` | `OfficeQrPoint` | 6 | 2 | 1 | 4 | — | стандартный makemigrations |
| `qr_codes.QrDisplaySession` | `qr_display_sessions` | `humotech.qr_codes` | `QrDisplaySession` | 2 | 3 | 0 | 3 | — | стандартный makemigrations |
| `devices.EmployeeDevice` | `employee_devices` | `humotech.devices` | `EmployeeDevice` | 1 | 2 | 1 | 4 | — | стандартный makemigrations |
| `qr_attendance.AttendanceEvent` | `attendance_events` | `humotech.attendance` | `AttendanceEvent` | 5 | 6 | 0 | 8 | qr_attendance (модели, сервис на следующем этапе) | UniqueConstraint(condition)/Index(condition) |
| `qr_attendance.AttendanceSession` | `attendance_sessions` | `humotech.attendance` | `AttendanceSession` | 4 | 5 | 0 | 5 | qr_attendance (модели, сервис на следующем этапе) | UniqueConstraint(condition)/Index(condition) |
| `qr_attendance.AttendanceCorrectionRequest` | `attendance_correction_requests` | `humotech.attendance` | `AttendanceCorrectionRequest` | 3 | 4 | 0 | 3 | — | стандартный makemigrations |
| `absences.AbsenceType` | `absence_types` | `humotech.absences` | `AbsenceType` | 1 | 1 | 1 | 3 | — | стандартный makemigrations |
| `absences.AbsenceRequest` | `absence_requests` | `humotech.absences` | `AbsenceRequest` | 5 | 5 | 0 | 4 | — | стандартный makemigrations |
| `absences.EmployeeAbsence` | `employee_absences` | `humotech.absences` | `EmployeeAbsence` | 2 | 4 | 0 | 4 | — | стандартный makemigrations |
| `absences.AbsenceDocument` | `absence_documents` | `humotech.absences` | `AbsenceDocument` | 1 | 4 | 1 | 4 | — | стандартный makemigrations |
| `absences.AbsenceAction` | `absence_actions` | `humotech.absences` | `AbsenceAction` | 1 | 4 | 0 | 3 | — | стандартный makemigrations |
| `absences.LeaveBalance` | `leave_balances` | `humotech.absences` | `LeaveBalance` | 4 | 3 | 1 | 4 | — | стандартный makemigrations |
| `files.File` | `files` | `humotech.files` | `File` | 3 | 3 | 0 | 3 | — | стандартный makemigrations |
| `telegram.TelegramAccount` | `telegram_accounts` | `humotech.telegram` | `TelegramAccount` | 1 | 2 | 2 | 4 | — | стандартный makemigrations |
| `knowledge_base.KnowledgeSource` | `knowledge_sources` | `humotech.knowledge` | `KnowledgeSource` | 7 | 7 | 0 | 7 | ai_assistant/services/publishing.py | UniqueConstraint(condition)/Index(condition), **RunSQL** (GIN to_tsvector) |
| `knowledge_base.KnowledgeChunk` | `knowledge_chunks` | `humotech.knowledge` | `KnowledgeChunk` | 2 | 2 | 1 | 6 | ai_assistant/services/{indexing,retrieval}.py | **RunSQL** (GIN to_tsvector), HnswIndex, VectorField |
| `knowledge_base.FaqEntry` | `faq_entries` | `humotech.knowledge` | `FaqEntry` | 3 | 6 | 0 | 4 | ai_assistant/services/retrieval.py | HnswIndex, VectorField |
| `knowledge_base.KnowledgeIndexJob` | `knowledge_index_jobs` | `humotech.knowledge` | `KnowledgeIndexJob` | 2 | 2 | 0 | 4 | ai_assistant/worker/queue.py | UniqueConstraint(condition)/Index(condition) |
| `ai_assistant.UnansweredQuestion` | `unanswered_questions` | `humotech.ai_assistant` | `UnansweredQuestion` | 3 | 6 | 1 | 5 | ai_assistant/services/escalation.py | стандартный makemigrations |
| `ai_assistant.LlmQueryLog` | `llm_query_logs` | `humotech.ai_assistant` | `LlmQueryLog` | 4 | 4 | 0 | 5 | ai_assistant/services/answer.py | UniqueConstraint(condition)/Index(condition) |
| `ai_assistant.AnswerFeedback` | `answer_feedback` | `humotech.ai_assistant` | `AnswerFeedback` | 1 | 3 | 1 | 4 | ai_assistant/services/answer.py | стандартный makemigrations |
| `questions.EmployeeQuestion` | `employee_questions` | `humotech.questions` | `EmployeeQuestion` | 2 | 4 | 0 | 4 | — | стандартный makemigrations |
| `notifications.Notification` | `notifications` | `humotech.notifications` | `Notification` | 2 | 2 | 0 | 4 | — | UniqueConstraint(condition)/Index(condition) |
| `audit.AuditLog` | `audit_logs` | `humotech.audit` | `AuditLog` | 0 | 3 | 0 | 5 | core/rbac.py AuditTrail | стандартный makemigrations |

## Объекты, требующие ручной миграции или `RunSQL`

### EXCLUDE USING gist

Выражаются через `django.contrib.postgres.constraints.ExclusionConstraint`.
Требуют расширения `btree_gist` — оно ставится операцией `BtreeGistExtension()`
в первой миграции.

* `employee_assignments.ex_employee_assignments_primary_overlap`
  ```sql
  EXCLUDE USING gist (employee_id WITH =, daterange(valid_from, valid_to, '[]'::text) WITH &&) WHERE (is_primary)
  ```
* `employee_schedule_assignments.ex_employee_schedule_assignments_overlap`
  ```sql
  EXCLUDE USING gist (employee_id WITH =, daterange(valid_from, valid_to, '[]'::text) WITH &&)
  ```

### GIN-индексы по `to_tsvector` — только `RunSQL`

`SearchVector` в Django оборачивает колонки в `COALESCE(...)`, чего в эталоне нет.
Это не разница записи, а разница поведения на NULL. Поэтому индекс объявляется
в `Meta.indexes` ради состояния и создаётся через
`SeparateDatabaseAndState` + `RunSQL` ради точного DDL — тогда и
`makemigrations --check` чист, и схема совпадает с эталоном.

Тот же класс причин уже отражён в `migrations/env.py`: эти два индекса
исключены из autogenerate Alembic.

* `knowledge_chunks.ix_knowledge_chunks_fts`
  ```sql
  CREATE INDEX ix_knowledge_chunks_fts ON public.knowledge_chunks USING gin (to_tsvector('simple'::regconfig, chunk_text))
  ```
* `knowledge_sources.ix_knowledge_sources_fts`
  ```sql
  CREATE INDEX ix_knowledge_sources_fts ON public.knowledge_sources USING gin (to_tsvector('simple'::regconfig, (((title)::text || ' '::text) || content)))
  ```

### HNSW-индексы pgvector

`pgvector.django.HnswIndex` с `m=16, ef_construction=64`. Расширение `vector`
ставится операцией `VectorExtension()`.

* `faq_entries.ix_faq_entries_embedding_cosine` — `hnsw (question_embedding vector_cosine_ops) WITH (m='16', ef_construction='64')`
* `knowledge_chunks.ix_knowledge_chunks_embedding_cosine` — `hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')`

### Частичные индексы

Всего 12. Уникальные выражаются через
`UniqueConstraint(fields=..., condition=Q(...))`, неуникальные — через
`Index(..., condition=Q(...))`.

* `attendance_events.uq_attendance_events_client_event` (уникальный) — `WHERE (client_event_id IS NOT NULL)`
* `attendance_events.uq_attendance_events_nonce` (уникальный) — `WHERE ((qr_nonce_hash IS NOT NULL) AND ((verification_status)::text = 'ACCEPTED'::text))`
* `attendance_sessions.uq_attendance_sessions_one_open` (уникальный) — `WHERE ((status)::text = 'OPEN'::text)`
* `calendar_exceptions.uq_calendar_exceptions_office_date` (уникальный) — `WHERE (office_id IS NOT NULL)`
* `calendar_exceptions.uq_calendar_exceptions_org_date` (уникальный) — `WHERE (office_id IS NULL)`
* `knowledge_index_jobs.ix_knowledge_index_jobs_queue` (обычный) — `WHERE ((status)::text = 'QUEUED'::text)`
* `knowledge_sources.uq_knowledge_sources_active_lineage` (уникальный) — `WHERE ((status)::text = 'ACTIVE'::text)`
* `llm_query_logs.ix_llm_query_logs_retention` (обычный) — `WHERE (anonymized_at IS NULL)`
* `notifications.ix_notifications_pending` (обычный) — `WHERE ((status)::text = 'PENDING'::text)`
* `roles.uq_roles_org_code` (уникальный) — `WHERE (organization_id IS NOT NULL)`
* `roles.uq_roles_system_code` (уникальный) — `WHERE (organization_id IS NULL)`
* `users.uq_users_employee_id` (уникальный) — `WHERE (employee_id IS NOT NULL)`

### Индексы по выражениям

* `attendance_events.ix_attendance_events_employee_time` — `(employee_id, occurred_at DESC)`
* `attendance_events.ix_attendance_events_office_time` — `(office_id, occurred_at DESC)`
* `attendance_events.ix_attendance_events_qr_point_time` — `(qr_point_id, occurred_at DESC)`
* `attendance_events.ix_attendance_events_status_time` — `(verification_status, occurred_at DESC)`
* `attendance_sessions.ix_attendance_sessions_employee_time` — `(employee_id, started_at DESC)`
* `attendance_sessions.ix_attendance_sessions_office_time` — `(office_id, started_at DESC)`
* `audit_logs.ix_audit_logs_actor_user` — `(actor_user_id, occurred_at DESC)`
* `audit_logs.ix_audit_logs_entity` — `(entity_type, entity_id, occurred_at DESC)`
* `audit_logs.ix_audit_logs_org_time` — `(organization_id, occurred_at DESC)`
* `llm_query_logs.ix_llm_query_logs_org_time` — `(organization_id, created_at DESC)`
* `organizations.uq_organizations_lower_code` — `(lower((code)::text))`
* `unanswered_questions.ix_unanswered_questions_top` — `(organization_id, occurrences_count DESC)`
* `users.uq_users_org_lower_email` — `(organization_id, lower((email)::text))`

### Специальные типы колонок

| Таблица.колонка | Тип | Способ |
| --- | --- | --- |
| `attendance_events.ip_address` | `inet` | `GenericIPAddressField` — даёт `inet` |
| `audit_logs.ip_address` | `inet` | `GenericIPAddressField` — даёт `inet` |
| `faq_entries.question_embedding` | `vector(1536)` | `pgvector.django.VectorField(dimensions=1536)` |
| `knowledge_chunks.embedding` | `vector(1536)` | `pgvector.django.VectorField(dimensions=1536)` |
| `office_networks.network_cidr` | `cidr` | **кастомное поле** `CidrField(db_type='cidr')` — встроенного нет |

### Составной первичный ключ

* `role_permissions (role_id, permission_id)` — `CompositePrimaryKey` (Django 5.2).
  На таблицу никто не ссылается внешним ключом, поэтому ограничения этой
  возможности не мешают. Запасной вариант — `SeparateDatabaseAndState` +
  `RunSQL`; добавлять суррогатный `id` нельзя, это изменило бы схему.

### `ON DELETE` у внешних ключей — отдельный проход

Django задаёт `on_delete` на стороне Python: удаление разбирает коллектор ORM,
а в DDL `ON DELETE` не попадает. В эталоне все 127 внешних ключей объявлены
с `ON DELETE RESTRICT`, `SET NULL` или `CASCADE`, и на это опираются требование
«физическое удаление объектов с историческими связями запретить» и два
существующих теста.

Поэтому после переноса моделей идёт отдельная миграция, которая переобъявляет
внешние ключи с исходным `ON DELETE`. Список берётся машинно из снимка схемы,
не переписывается руками.

| `ON DELETE` | Внешних ключей |
| --- | ---: |
| `CASCADE` | 6 |
| `RESTRICT` | 107 |
| `SET NULL` | 14 |

## Сервисы и тесты

Переносятся на Django ORM (этап 6), бизнес-логика остаётся в слое сервисов:

| Сервис | Django ORM вместо SQLAlchemy |
| --- | --- |
| `core/rbac.py` `AccessControl`, `AuditTrail` | QuerySet вместо `select()` |
| `core/service.py` `BaseService.atomic` | `transaction.atomic(savepoint=True)` |
| `core/pagination.py` | keyset остаётся, `tuple_()` -> `Q`-выражения |
| `core/errors.py` | `IntegrityError` psycopg вместо SQLAlchemy |
| `regions/offices/schedules/employees` service | `select_related`/`prefetch_related` |
| `ai_assistant/*` | отдельный этап; Pydantic для LLM остаётся |
| `ai_assistant/worker/queue.py` | `select_for_update(skip_locked=True)` |

Тесты (351: 190 unit, 161 integration) переносятся с сохранением сценариев;
таблица соответствия старый тест -> новый ведётся на этапе 7. Все тесты с базой
остаются на PostgreSQL 18 + pgvector, SQLite не используется.

## Порядок работ

1. Калибровка инструмента сравнения — **выполнено**: снимок Alembic против
   свежей Alembic-базы даёт ноль расхождений, а внесённые вручную шесть
   расхождений инструмент находит.
2. Отдельная база `humotech_django`; `humotech` и `humotech_test` не трогаются.
3. Каркас Django + пробное приложение `organizations`, сверка его схемы до нуля.
4. Остальные приложения в порядке зависимостей.
5. Сложные объекты и проход по `ON DELETE`.
6. Полная сверка схем.
7. Сервисы, затем тесты, затем DRF.

---

## Результат переноса схемы (этапы 3–5)

Схема, построенная Django-миграциями на пустой базе, **совпадает с эталоном
Alembic полностью**: 44 бизнес-таблицы, расхождений ноль.

```
$ python -m scripts.schema_snapshot compare <alembic> <django>
СХЕМЫ СОВПАДАЮТ: 44 бизнес-таблиц, расхождений нет
```

### Django-приложения

19 доменных приложений плюс `humotech.core` (без моделей — держит миграцию
расширений PostgreSQL и общие примеси). RBAC вынесен в отдельное приложение
`humotech.rbac`: `accounts` и `employees` ссылаются друг на друга, поэтому
Django выносит их внешние ключи в отдельную миграцию, а составной первичный
ключ `role_permissions` так создать нельзя — он объявляется вместе с таблицей.

### Что потребовало нестандартных решений

| Находка | Почему это важно | Решение |
| --- | --- | --- |
| `django.db.models.functions.Now` даёт `statement_timestamp()` | Значение меняется на каждом операторе: строки одной транзакции получили бы РАЗНОЕ время создания. На равенстве этого времени держится ключ постраничного вывода | `humotech/core/functions.TransactionNow` — точный `now()` |
| Django не выводит `ON DELETE` в DDL и добавляет `DEFERRABLE INITIALLY DEFERRED` | Запрет физического удаления объектов с историей держится на `ON DELETE RESTRICT` в базе. Прямой `DELETE` мимо ORM обязан отклоняться | Миграция `core/0002_raw_schema_objects` переобъявляет все 127 ключей (RESTRICT 107, SET NULL 14, CASCADE 6) |
| `models.E034`: имя индекса не длиннее 30 символов | Это предел идентификаторов Oracle; у 58 из 96 индексов схемы имена длиннее | Проверка отключена: проект работает только с PostgreSQL (предел 63 байта), и настройки отвергают другой движок |
| `~Q(a=F("b"))` даёт `NOT (a = b AND a IS NOT NULL)` | Смысл тот же, текст другой — а схема сверяется по тексту ограничения | `humotech/core/constraints.raw_check` для простых выражений |
| `SearchVector` оборачивает колонки в `COALESCE(...)` | Разница поведения на NULL, а не записи | Два GIN-индекса создаются точным SQL в `core/0002`; в состоянии моделей их нет, поэтому `makemigrations --check` чист |
| `auth.E003`: `USERNAME_FIELD` обязан быть уникальным | Почта уникальна внутри организации, глобально — нет | Проверка отключена, вход выполняет свой backend по паре «организация + почта» |
| `PermissionsMixin` добавил бы в `users` колонку `is_superuser` и таблицы связей | Это вторая система прав рядом с `roles` + `user_role_scopes` | Только `AbstractBaseUser`; `is_staff`, `is_superuser` и `has_perm` выводятся из своих ролей |
| Нет встроенного поля для `cidr` | `GenericIPAddressField` даёт `inet` и не проверяет, что это сеть | `humotech/core/fields.CidrField` |

### Что перенеслось без единой правки

Оба `EXCLUDE USING gist` с `daterange(..., '[]')` и условием, все 13 частичных
уникальных индексов, индексы по `lower()`, HNSW с `m=16, ef_construction=64`,
`vector(1536)`, составной первичный ключ `role_permissions`, все 92 `CHECK`,
20 уникальных ограничений и убывающие индексы по времени.

### Проверки

| Проверка | Результат |
| --- | --- |
| `manage.py check` | без замечаний (58 отключено осознанно) |
| `manage.py check --deploy` | без замечаний, кроме предупреждения о тестовом `SECRET_KEY` |
| `manage.py makemigrations --check --dry-run` | `No changes detected` |
| `migrate` на пустой базе | 42 миграции |
| откат `core/0002` на одноразовой базе | 127 ключей с `ON DELETE` -> 0, GIN-индексы сняты |
| откат приложения до нуля и обратно | таблица снята и восстановлена |
| повторный `migrate` | схема снова совпадает с эталоном |
