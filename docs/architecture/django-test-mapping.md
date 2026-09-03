# Соответствие тестов: SQLAlchemy -> Django

Этап 7 перехода. Ни один сценарий не потерян и ни один не заменён заглушкой:
проверяется то же поведение, меняется только способ обращения к базе.

Оба набора пока живут рядом. Старый — перекрёстная проверка: он подтверждает,
что после переноса уцелело **поведение**, а не только схема. Схему стережёт
отдельный тест, сверяющий её с эталоном на каждом прогоне.

```
# Django (боевой набор)
pytest -c pytest_django.ini

# прежний набор — перекрёстная проверка
pip install -r requirements-legacy.txt
TEST_DATABASE_URL=postgresql+psycopg://... pytest
```

## Итог

| Набор | Тестов |
| --- | ---: |
| Прежний (SQLAlchemy + Alembic) | 351 |
| Django | **380** |

Разница — новые тесты, которых раньше не было: контракт ORM, сверка схемы
с эталоном, REST API и несколько регрессий, найденных при переносе.

## Файл к файлу

| Прежний тест | Новый | Тестов | Как перенесён |
| --- | --- | ---: | --- |
| `tests/unit/test_ai_cache.py` | `django_tests/test_ai_cache.py` | 7 | без изменений: кэш от ORM не зависит |
| `tests/unit/test_ai_personal_data.py` | `django_tests/test_ai_personal_data.py` | 4 | без изменений: разбор вопроса — чистая логика |
| `tests/unit/test_ai_retrieval_precedence.py` | `django_tests/test_ai_retrieval_precedence.py` | 8 | сервис больше не принимает сессию |
| `tests/unit/test_ai_safety.py` | `django_tests/test_ai_safety.py` | 10 | без изменений |
| `tests/unit/test_ai_providers_and_prompts.py` | `django_tests/test_ai_providers_and_prompts.py` | 24 | проверка «имена моделей не зашиты» смотрит на `humotech/`; тест пустого баланса читает журнал из базы, а не из поддельной сессии |
| `tests/unit/test_ai_pipeline.py` | `django_tests/test_ai_pipeline.py` | 18 | поддельная сессия заменена живой базой: журнал и эскалацию пишет ORM |
| `tests/unit/test_ai_personal_service.py` | `django_tests/test_ai_personal_service.py` | 32 | то же; расчёты минут и границ периодов остались без базы |
| `tests/unit/test_hr_validation.py` | `django_tests/test_hr_validation.py` | 59 | подделка `IntegrityError` строит цепочку `__cause__` — Django оборачивает ошибку драйвера |
| `tests/integration/test_permission_scopes.py` | `django_tests/test_permission_scopes.py` | 5 | отдельные функции области стали методами `AccessControl` |
| `tests/integration/test_schema_constraints.py` | `django_tests/test_schema_constraints.py` | 15 | `db.add` -> `objects.create`, `begin_nested` -> `transaction.atomic` |
| `tests/integration/test_qr_attendance.py` | `django_tests/test_qr_attendance.py` | 14 | сервис QR больше не принимает сессию |
| `tests/integration/test_ai_knowledge.py` | `django_tests/test_ai_knowledge.py` | 15 | то же |
| `tests/integration/test_ai_personal_data.py` | `django_tests/test_ai_personal_data_live.py` | 20 | то же; добавлен явный сброс кэша прав |
| `tests/integration/test_ai_worker_queue.py` | `django_tests/test_ai_worker_queue.py` | 10 | **переписан**: проверка SKIP LOCKED требует двух соединений, в Django это `django_db(transaction=True)` и отдельное соединение |
| `tests/integration/test_hr_org_structure.py` | `django_tests/test_hr_org_structure.py` | 27 | сервисы без сессии, запросы через ORM |
| `tests/integration/test_hr_schedules.py` | `django_tests/test_hr_schedules.py` | 22 | pydantic-схемы дней заменены dataclass-DTO |
| `tests/integration/test_hr_employees.py` | `django_tests/test_hr_employees.py` | 35 | счётчик запросов — через `connection.execute_wrapper` |
| — | `django_tests/test_orm_contract.py` | 5 | **новый**: три различия Django ORM, каждое меняло бы форму всех сервисов |
| — | `django_tests/test_schema_parity.py` | 3 | **новый**: схема сверяется с эталоном на каждом прогоне |
| — | `django_tests/test_api_hr.py` | 19 | **новый**: REST-слой целиком |

## Что изменилось в подходе

**Часть модульных тестов стала работать с базой.** Раньше журнал обращений
и эскалацию писала поддельная сессия, у которой можно было спросить, что в неё
положили. Теперь их пишет ORM, и проверять их логичнее по настоящим строкам —
заодно это подтверждает, что записи проходят ограничения схемы. Подделка
сессии проверяла бы намерение, а не результат.

**Появился предохранитель на движок базы.** Сессионная фикстура падает, если
`connection.vendor` не `postgresql` или в базе нет `btree_gist` и `vector`.
Без неё достаточно одной правки настроек, чтобы весь набор прошёл на SQLite —
зелёный и ничего не проверяющий.

**Разовая сверка схемы стала постоянной.** `test_schema_parity.py` сравнивает
схему Django с эталоном Alembic и отдельно следит, что все 127 внешних ключей
сохранили `ON DELETE`. Без этого следующий `makemigrations` мог бы тихо
изменить тип колонки или потерять ограничение.

## Регрессии, закреплённые при переносе

| Проверка | Что ловит |
| --- | --- |
| `test_rows_of_one_transaction_share_created_at` | подмену `now()` на `statement_timestamp()`: строки одной транзакции получили бы разное время создания, и ключ постраничного вывода перестал бы работать |
| `test_every_foreign_key_keeps_its_on_delete_action` | потерю `ON DELETE`: прямой `DELETE` мимо ORM снёс бы историю |
| `test_integrity_error_outside_atomic_leaves_connection_usable` | перехват ошибки внутри блока `atomic()` — там любой следующий запрос даёт `TransactionManagementError` |
| `test_composite_primary_key_create_and_get` | поведение составного ключа `role_permissions` на уровне ORM, а не только DDL |
| `test_two_workers_do_not_take_the_same_job` | неработающий `SKIP LOCKED`: два воркера взяли бы одну задачу |
| `test_employee_list_does_not_grow_queries_with_page_size` | N+1 в списке сотрудников — счётчиком операторов, а не наличием `select_related` в коде |
