# backend-api

Единственный сервис, который подключается к PostgreSQL. CRM, Telegram-бот
и QR-дисплей работают только через его HTTP API.

Стек: PostgreSQL 15+, SQLAlchemy 2.0, Alembic, psycopg 3, pytest.

Схема базы целиком описана в [`docs/database/schema.md`](../../docs/database/schema.md).

## Установка

```bash
cd apps/backend-api
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Linux: .venv/bin/python
cp .env.example .env        # прописать DATABASE_URL и TEST_DATABASE_URL
```

## Миграции

```bash
# создать базы (один раз)
psql -U postgres -c "CREATE DATABASE humotech"
psql -U postgres -c "CREATE DATABASE humotech_test"

# применить схему
PYTHONPATH=. .venv/Scripts/python.exe -m alembic upgrade head

# посмотреть SQL, ничего не применяя
PYTHONPATH=. .venv/Scripts/python.exe -m alembic upgrade head --sql

# откатить
PYTHONPATH=. .venv/Scripts/python.exe -m alembic downgrade base
```

Первая миграция сама создаёт расширение `btree_gist` — оно нужно
`EXCLUDE`-ограничениям, запрещающим пересечение назначений и графиков.
Поэтому у роли, применяющей миграцию, должно быть право `CREATE EXTENSION`
(суперпользователь либо заранее созданное расширение в базе).

## Seed-данные

```bash
# только глобальные справочники: разрешения и системные роли
PYTHONPATH=. .venv/Scripts/python.exe -m scripts.seed

# плюс создать организацию и типы отсутствий для неё
PYTHONPATH=. .venv/Scripts/python.exe -m scripts.seed \
    --create-organization HUMOTECH --name "HUMOTECH" --timezone Asia/Dushanbe
```

Скрипт идемпотентен — повторный запуск ничего не дублирует.

## Тесты

Разовая подготовка тестовой базы (нужен доступ суперпользователя ровно один раз —
дальше тесты обходятся обычной ролью):

```bash
psql -U postgres -c "CREATE ROLE humo_test LOGIN PASSWORD 'humo_test_pwd'; CREATE DATABASE humotech_test OWNER humo_test;"
psql -U postgres -d humotech_test -c "CREATE EXTENSION IF NOT EXISTS btree_gist; GRANT ALL ON SCHEMA public TO humo_test;"
```

Запуск:

```bash
TEST_DATABASE_URL=postgresql+psycopg://humo_test:humo_test_pwd@127.0.0.1:5432/humotech_test \
  PYTHONPATH=. .venv/Scripts/python.exe -m pytest
```

Тестам нужен настоящий PostgreSQL: схема опирается на `CIDR`, `INET`, `JSONB`,
частичные индексы, `gen_random_uuid()` и `EXCLUDE ... USING gist`. SQLite ничего
из этого не умеет, поэтому подмена базы была бы самообманом.

Схема в тестах разворачивается **настоящей миграцией Alembic** — так проверяется
и она тоже, а не только описание моделей. Каждый тест выполняется в своей
транзакции и откатывается.

Перед прогоном `conftest` сносит из базы все таблицы, но не схему: вместе со
схемой удалилось бы расширение `btree_gist`, а его пересоздание требует прав
суперпользователя. Поэтому расширение ставится один раз при заведении базы.

Без `TEST_DATABASE_URL` тесты не падают, а пропускаются с подсказкой. Имя базы
обязано содержать `test`: `conftest` удаляет из неё все таблицы и отказывается
работать с базой, чьё имя на тестовое не похоже.

## Структура

```
src/
├── core/
│   ├── config/settings.py        всё из .env
│   ├── database/
│   │   ├── base.py               Base, миксины, конвенция имён ограничений
│   │   ├── enums.py              допустимые значения статусов (VARCHAR + CHECK)
│   │   ├── registry.py           сборка Base.metadata из всех модулей
│   │   └── session.py            движок и сессии
│   └── permissions/
│       ├── catalog.py            каталог разрешений и их привязка к ролям
│       └── scopes.py             какие офисы видит пользователь
├── modules/
│   ├── organizations/  regions/  offices/  departments/  positions/
│   ├── employees/  devices/  users/  roles/
│   ├── schedules/
│   ├── qr_codes/                 office_qr_points, qr_display_sessions
│   ├── qr_attendance/            attendance_events, sessions, корректировки
│   │   └── service.py            серверная обработка скана
│   ├── absences/  files/
│   └── telegram/  knowledge_base/  questions/  notifications/  audit/
migrations/versions/0001_initial_schema.py
scripts/seed.py
tests/integration/
```

## Правила, которые нельзя нарушать

1. **Только этот сервис ходит в базу.** Появится второй — модель прав рассыплется.
2. **Вся бизнес-логика здесь**, а не в клиентах: расчёт часов, опоздания,
   больничные, отпуска, права HR, обработка QR-событий, аналитика, аудит.
3. **`verification_status` ставит сервер.** Клиент присылает наблюдения
   (координаты, IP, время), а не готовый результат проверки.
4. **Офис определяется по QR-точке.** `office_id`, присланный клиентом,
   не используется никогда.
5. **Ничего не удаляем физически** — архивирование и статусы.
6. **`attendance_events` неизменяема.** Исправления идут через
   `attendance_correction_requests`.
