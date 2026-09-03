# Backend HR CRM: организационная структура, офисы, сотрудники, графики

Документ описывает прикладной слой, через который HR работает с организационной
структурой и кадровыми данными. Схема базы описана отдельно —
в [`docs/database/schema.md`](../database/schema.md); здесь только поведение.

## 1. Где это лежит

```
apps/backend-api/src/
├── core/
│   ├── errors.py       доменные ошибки с устойчивыми кодами
│   ├── rbac.py         Actor, AccessControl, AuditTrail
│   ├── service.py      BaseService: транзакции и перевод ошибок базы
│   ├── validation.py   проверки, которых не может сделать схема
│   └── pagination.py   keyset-пагинация
└── modules/
    ├── regions/{service,schemas}.py
    ├── offices/{service,schemas}.py
    ├── schedules/{service,schemas}.py
    └── employees/{service,schemas}.py
```

HTTP-слоя в проекте нет — как и у AI-модуля, «API» здесь означает сервис плюс
типизированные pydantic-схемы. Когда появится веб-фреймворк, маршруты станут
тонкой обёрткой: разбор запроса, вызов сервиса, `DomainError.as_dict()` и
`DomainError.http_status` в ответ.

## 2. Операции

### Регионы — `RegionService`

| Операция | Метод | Разрешение |
| --- | --- | --- |
| Список | `list(actor, search, status, limit, cursor)` | `regions.read` |
| Карточка | `get(actor, region_id)` | `regions.read` |
| Создание | `create(actor, RegionCreateRequest)` | `regions.manage` |
| Изменение | `update(actor, region_id, RegionUpdateRequest)` | `regions.manage` |
| Выключение | `deactivate(actor, region_id)` → `INACTIVE` | `regions.manage` |
| Включение | `reactivate(actor, region_id)` → `ACTIVE` | `regions.manage` |

### Офисы — `OfficeService`

| Операция | Метод | Разрешение |
| --- | --- | --- |
| Список | `list(actor, search, status, region_id, limit, cursor)` | `offices.read` |
| Карточка | `get(actor, office_id)` | `offices.read` |
| Создание | `create(actor, OfficeCreateRequest)` | `offices.manage` |
| Изменение | `update(actor, office_id, OfficeUpdateRequest)` | `offices.manage` |
| Выключение | `deactivate(actor, office_id)` → `INACTIVE` | `offices.manage` |
| Включение | `reactivate(actor, office_id)` → `ACTIVE` | `offices.manage` |
| Закрытие насовсем | `close(actor, office_id, closed_at)` → `CLOSED` | `offices.manage` |

`INACTIVE` обратим, `CLOSED` — нет. Оба значения уже были в схеме, новых
статусов не вводилось.

### Графики — `WorkScheduleService`

| Операция | Метод | Разрешение |
| --- | --- | --- |
| Список | `list(actor, search, status, limit, cursor)` | `schedules.read` |
| Карточка с днями и перерывами | `get(actor, schedule_id)` | `schedules.read` |
| Создание | `create(actor, WorkScheduleCreateRequest)` | `schedules.manage` |
| Изменение | `update(actor, schedule_id, WorkScheduleUpdateRequest)` | `schedules.manage` |
| Выключение / включение | `deactivate` / `reactivate` | `schedules.manage` |
| Назначение сотруднику | `assign_to_employee(actor, employee_id, schedule_id, valid_from)` | `schedules.manage` |
| История назначений | `history(actor, employee_id)` | `schedules.read` |

### Сотрудники — `EmployeeService`

| Операция | Метод | Разрешение |
| --- | --- | --- |
| Список | `list(actor, search, status, office_id, region_id, at, limit, cursor)` | `employees.read` |
| Полная карточка | `get(actor, employee_id, at)` | `employees.read` |
| История назначений | `assignment_history(actor, employee_id)` | `employees.read` |
| Приём | `create(actor, EmployeeCreateRequest)` | `employees.manage` |
| Изменение данных | `update(actor, employee_id, EmployeeUpdateRequest)` | `employees.manage` |
| Перевод (офис, отдел, должность, руководитель) | `change_assignment(actor, employee_id, AssignmentChangeRequest)` | `employees.manage` |
| Деактивация | `deactivate(actor, employee_id)` → `SUSPENDED` | `employees.manage` |
| Возврат к работе | `reactivate(actor, employee_id)` → `ACTIVE` | `employees.manage` |
| Увольнение | `terminate(actor, employee_id, termination_date)` → `TERMINATED` | `employees.archive` |

Карточка отдаёт текущее назначение, действующий график, состояние привязки
Telegram и полную историю переводов.

## 3. Права и область видимости

Разрешение отвечает на вопрос «что можно делать», запись в `user_role_scopes` —
«с чьими данными». Обе части обязательны: у регионального HR может быть
`employees.manage`, но чужой офис ему всё равно закрыт.

Область раскрывается так:

```
region_id IS NULL и office_id IS NULL  →  вся организация
указан region_id                        →  все офисы этого региона
указан office_id                        →  только этот офис
```

Три правила, которые легко нарушить:

* **Пустая область — это «ничего», а не «всё».** `visible_office_ids` возвращает
  `None` для доступа ко всей организации и пустое множество для отсутствия
  доступа. Проверка `if visible:` склеивает эти случаи и молча выдаёт всю
  организацию. Закреплено тестом
  `test_empty_scope_gives_nothing_not_everything`.
* **Чужой объект отвечает как несуществующий.** Запрос региона, офиса, графика
  или сотрудника соседней организации даёт `NotFound`, а не `PermissionDenied`:
  иначе перебором идентификаторов можно пересчитать чужие записи.
* **Внутри своей организации** объект вне области видимости даёт
  `PermissionDenied` — здесь скрывать факт существования незачем.

Сотрудник виден по офису своего **текущего основного назначения**. Определение
«текущего» вынесено в `current_primary_assignment_predicate` и одно на весь
модуль: иначе список и карточка могли бы разойтись во мнении о том, где человек
работает.

## 4. Бизнес-правила

| Правило | Где проверяется |
| --- | --- |
| Офис принадлежит организации сотрудника | `AccessControl.require_office` |
| Регион в запросе совпадает с регионом офиса | `EmployeeService.create` |
| График принадлежит организации сотрудника | `AccessControl.require_schedule` |
| Отдел относится к тому же офису, что и назначение | `EmployeeService._validate_department` |
| В неактивный офис нельзя назначить | `_require_assignable_office` |
| Неактивный график нельзя назначить | `assign_to_employee` |
| Неактивные отдел и должность нельзя назначить | `_validate_department` / `_validate_position` |
| В неактивный регион нельзя добавить офис | `OfficeService.create` |
| Дата увольнения не раньше даты приёма | `terminate` + `ck_employees_termination_after_hire` |
| Перевод не раньше даты приёма | `change_assignment` |
| Часовой пояс существует в базе IANA | `validate_timezone` |
| Интервал рабочего дня корректен | `validate_day_interval` |
| Перерыв лежит внутри рабочего дня | `validate_break` |
| Уникальные поля дают понятную ошибку, а не `IntegrityError` | `translate_integrity_error` |

### Периоды и почему смена офиса — это две строки

`employee_assignments` и `employee_schedule_assignments` защищены
EXCLUDE-ограничениями по `daterange(valid_from, valid_to, '[]')`.
Границы **включительные**, ограничения **не отложенные**. Отсюда:

1. Старый период закрывается датой `valid_from - 1 день`. Закрытие той же
   датой, с которой начинается новый период, считалось бы пересечением.
2. Между закрытием старого периода и вставкой нового стоит явный `flush()`:
   ограничение проверяется в момент выполнения оператора, и без этого
   SQLAlchemy может отправить INSERT первым.
3. Обе операции идут внутри `SAVEPOINT` (`BaseService.atomic`). Если вторая
   часть падает, первая откатывается — у сотрудника не остаётся закрытого
   назначения без действующего. Закреплено тестом
   `test_failed_transfer_leaves_previous_assignment_untouched`.

### Что означает «не удалять историю»

Ничто из перечисленного не удаляет строки:

* деактивация региона, офиса, графика — смена `status`;
* деактивация сотрудника — `employment_status = SUSPENDED`;
* увольнение — `employment_status = TERMINATED`, проставляется
  `termination_date`, открытые периоды назначения и графика **закрываются**
  этой датой. Строки остаются, отметки о входе и выходе не трогаются.

Закрытие периода принято сознательно: без него уволенный человек навсегда
остался бы «работающим сейчас» в любом отчёте по текущему составу.

Физического удаления сервисы не предоставляют вовсе, а на уровне схемы его
запрещают внешние ключи с `ON DELETE RESTRICT` — это проверено тестами
`test_office_with_history_cannot_be_physically_deleted` и
`test_region_with_offices_cannot_be_physically_deleted`.

## 5. Ошибки

| Класс | `code` | HTTP | Когда |
| --- | --- | --- | --- |
| `ValidationFailed` | `validation_error` | 400 | данные не прошли проверку |
| `PermissionDenied` | `forbidden` | 403 | нет разрешения либо объект вне области |
| `NotFound` | `not_found` | 404 | объект не существует либо принадлежит другой организации |
| `Conflict` | `conflict` | 409 | состояние не позволяет операцию, нарушена уникальность |

`DomainError.as_dict()` даёт готовое тело ответа
`{"error": {"code", "message", "details"}}`. Клиент различает ситуации по `code`:
текст сообщения можно переводить и переписывать, код — нет.

`IntegrityError` наружу не выходит никогда: `translate_integrity_error`
переводит его по имени нарушенного ограничения в понятное сообщение.
Неизвестное ограничение тоже даёт `Conflict`, просто с общим текстом.

## 6. Списки

* **Пагинация — keyset**, ключ `(created_at DESC, id DESC)`. Курсор
  base64-строка, испорченный даёт `validation_error`. `OFFSET` не используется:
  он линейно замедляется и на живых данных показывает одну запись дважды.
  Обе части ключа обязательны — у записей одной транзакции `created_at`
  совпадает до микросекунды.
* **Поиск**: регионы и офисы — по коду, названию и адресу; сотрудники — по
  имени, фамилии, отчеству, табельному номеру, почте и телефону.
* **Фильтры**: статус везде; регион для офисов; офис, регион и статус для
  сотрудников — всегда в пределах разрешённой области.
* **N+1 отсутствует**: справочники страницы догружаются одним запросом на всю
  страницу. Проверено счётчиком операторов, а не наличием `selectinload` в коде
  (`test_employee_list_does_not_grow_queries_with_page_size`).

## 7. Аудит

Все изменения пишутся в существующий `audit_logs`: организация, пользователь,
действие, тип и идентификатор объекта, время, снимки `before`/`after`.

```
region.create   region.update   region.deactivate   region.reactivate
office.create   office.update   office.deactivate   office.reactivate   office.close
schedule.create schedule.update schedule.deactivate schedule.reactivate
employee.create employee.update employee.assignment.change
employee.schedule.assign
employee.deactivate employee.reactivate employee.terminate
```

`AuditTrail.FORBIDDEN_FIELDS` вычищает пароли, хеши, токены и ключи до записи —
фильтр работает на составе полей, а не на добросовестности вызывающего.

## 8. Миграции

Новая миграция **не понадобилась**. Схема, созданная миграциями `0001` и `0002`,
покрывает задачу целиком: `regions`, `offices`, `departments`, `positions`,
`employees`, `employee_assignments`, `work_schedules`, `schedule_days`,
`schedule_breaks`, `employee_schedule_assignments`, `telegram_accounts`,
`audit_logs`. Новых разрешений тоже не потребовалось — все нужные коды уже есть
в `core/permissions/catalog.py`.

Проверено сравнением моделей с фактической схемой живой базы через
`alembic.autogenerate.produce_migrations`: расхождений ноль.

## 9. Что осталось на следующий этап

* привязка сотрудника к Telegram (сейчас backend только показывает состояние
  привязки, создавать её нечем);
* QR-вход и выход;
* HTTP-слой поверх сервисов;
* `Actor` продублирован в `ai_assistant/use_cases/crm.py` — объединить с
  `core/rbac.Actor`, когда AI-модуль снова будет открыт для правок.
