# AI-ассистент HUMOTECH

Отвечает сотрудникам на вопросы о правилах компании — **только** на основании
подтверждённых HR документов. Технология: RAG (retrieval-augmented generation),
не дообучение модели.

**Модуль по умолчанию выключен:** `AI_ASSISTANT_ENABLED=false`. Пока рубильник
опущен, провайдеры не создаются и наружу не уходит ни одного запроса.

---

## 1. Что модуль делает и чего не делает

**Делает:** находит подтверждённые правила под область сотрудника (офис, регион,
язык), отдаёт готовый ответ из FAQ без обращения к модели, а при отсутствии
точного совпадения — просит модель ответить строго по найденным фрагментам.

**Не делает:**

- не придумывает внутренние правила компании — только то, что есть в базе;
- не отвечает на личные вопросы («сколько я отработал») — это отдельный сервис
  с авторизованным SQL по `employee_id`;
- не выбирает между противоречащими правилами — это решение HR;
- не доверяет содержимому документов как инструкциям.

Если подтверждённого ответа нет, сотрудник получает ровно этот текст:

> Я не нашёл подтверждённой информации по этому вопросу. Ваш вопрос передан HR.

и вопрос сохраняется в `unanswered_questions` для HR.

---

## 2. Архитектура

```
                 Telegram-бот                     HR CRM
                      │                              │
              use_cases/bot.py               use_cases/crm.py
                      │                              │
        ┌─────────────┴──────────────────────────────┴───────────┐
        │                     AnswerService                       │
        │  порядок шагов от дешёвого к дорогому                   │
        └───┬─────────┬──────────┬────────────┬───────────┬───────┘
            │         │          │            │           │
   PersonalDataRouter │   CacheService  RetrievalService  │
            │         │          │            │           │
   PersonalDataQuery  │      (память,     pgvector +   QuestionEscalation
       Service        │    ключ с ревизией)   FTS          Service
                      │                        │
              EmbeddingProvider ──────► PostgreSQL (единственное хранилище)
                      │
                 LLMProvider
                      │
        ┌─────────────┴─────────────┐
   OpenAI*Provider            Fake*Provider (тесты)
```

**OpenAI SDK импортируется ровно в одном файле** — `providers/openai_provider.py`.
Бизнес-логика видит только абстракции `LLMProvider` и `EmbeddingProvider`.
Смена провайдера — новая реализация двух классов и одна ветка в фабрике.

**Источник истины — PostgreSQL.** OpenAI File Search не используется: нам нужен
контроль версий и момента публикации, а не чужое непрозрачное хранилище.

### Расположение

```
apps/backend-api/src/modules/
├── knowledge_base/models.py     knowledge_sources, knowledge_chunks,
│                                faq_entries, knowledge_index_jobs
└── ai_assistant/
    ├── config.py                все настройки из .env
    ├── errors.py                ошибки модуля (SDK наружу не протекает)
    ├── models.py                unanswered_questions, llm_query_logs,
    │                            answer_feedback
    ├── schemas.py               типизированный контракт ответа
    ├── worker.py                фоновая индексация (SKIP LOCKED)
    ├── prompts/                 версионируемый системный промпт
    ├── providers/               base / openai_provider / fake
    ├── services/                safety, scoping, cache, chunking,
    │                            ingestion, publishing, retrieval,
    │                            personal_data, escalation, answer,
    │                            metrics, rate_limit
    └── use_cases/               bot.py, crm.py
```

---

## 3. Поток данных

### Ответ на вопрос

```
вопрос сотрудника
   │
   ├─ 1. область сотрудника: офис, регион, язык        scoping.py
   ├─ 2. лимит частоты                                 rate_limit.py
   ├─ 3. длина, секреты, попытка инъекции              safety.py
   │
   ├─ 4. личный вопрос? ──да──► PersonalDataQueryService ──► ответ (без RAG,
   │                                                          без кэша)
   ├─ 5. кэш (ключ включает knowledge_revision) ──попал──► ответ
   │
   ├─ 6. эмбеддинг вопроса                             EmbeddingProvider
   │
   ├─ 7. точный FAQ выше порога? ──да──► approved_answer (МОДЕЛЬ НЕ ВЫЗЫВАЕТСЯ)
   │
   ├─ 8. гибридный поиск: pgvector + FTS + фильтры     retrieval.py
   │        │
   │        ├─ пусто ────────────► unanswered_questions ──► ответ «передан HR»
   │        └─ конфликт правил ──► unanswered_questions ──► ответ «передан HR»
   │
   ├─ 9. вызов основной модели                         gpt-модель из .env
   ├─10. неоднозначно? ──► ОДИН вызов резервной модели
   │
   └─11. журнал: токены, задержка, источники, результат  llm_query_logs
```

### Публикация знания

```
HR создаёт DRAFT
   │
   ├─ enqueue_indexing: источник -> INDEXING, задача -> knowledge_index_jobs
   │
   ├─ воркер берёт задачу (SELECT ... FOR UPDATE SKIP LOCKED)
   │     ├─ режет текст на смысловые куски (по абзацам)
   │     ├─ считает эмбеддинги ТОЛЬКО для новых кусков (по content_hash)
   │     └─ пишет knowledge_chunks
   │
   ├─ успех  -> задача SUCCEEDED, версия готова к публикации
   └─ провал -> версия ERROR; СТАРАЯ ACTIVE-версия продолжает отвечать
         │
         └─ publish() в ОДНОЙ транзакции:
               новая -> ACTIVE
               старая -> ARCHIVED
               organizations.knowledge_revision += 1
                     │
                     └─ весь прежний кэш перестаёт адресоваться
```

---

## 4. Приоритет правил и конфликты

Порядок жёсткий и детерминированный, модель в нём не участвует:

1. **офис > регион > глобальное** — правило офиса перекрывает региональное,
   региональное перекрывает общее;
2. на одном уровне побеждает более высокий `priority`, заданный HR;
3. при равном приоритете внутри одного документа — более новая
   опубликованная версия (`published_at`);
4. если на одном уровне сопоставимо подходят **разные** документы с равным
   приоритетом — статус `ESCALATED`, вопрос уходит HR.

Порог «сопоставимости» — `AI_CONFLICT_SCORE_DELTA` (по умолчанию 0.05):
второй документ считается конкурирующим, если его балл отличается от лучшего
не больше чем на эту величину. Иначе escalation срабатывал бы почти на каждом
вопросе, где нашлось два документа.

**Смысловые противоречия ассистент не ищет.** Определить, что два текста
противоречат друг другу, может только модель — то есть ровно то суждение,
которое ей запрещено. Приоритет и правильный источник задаёт HR.

---

## 5. Подключение pgvector

Расширение `vector` **не входит** в Windows-инсталлятор PostgreSQL от EDB,
поэтому локальная разработка идёт в контейнере:

```bash
docker compose -f infrastructure/docker/docker-compose.yml up -d
```

Контейнер `pgvector/pgvector:pg18` слушает **порт 5433** — системный PostgreSQL
на 5432 не затрагивается. При первом старте `initdb/01_databases.sql` создаёт
базу `humotech_test` и ставит `btree_gist` и `vector` в обе базы.

Проверка:

```bash
docker exec humotech_postgres psql -U humotech -d humotech \
  -c "SELECT extname FROM pg_extension"
```

---

## 6. Миграции

| Миграция | Что делает |
|---|---|
| `0001_initial_schema` | базовая схема HR-системы, 38 таблиц |
| `0002_ai_assistant` | `CREATE EXTENSION vector`; `knowledge_articles` → `knowledge_sources`; шесть новых таблиц; HNSW-индексы; `organizations.knowledge_revision` |

```bash
cd apps/backend-api
PYTHONPATH=. .venv/Scripts/python.exe -m alembic upgrade head
PYTHONPATH=. .venv/Scripts/python.exe -m alembic upgrade head --sql   # посмотреть SQL
PYTHONPATH=. .venv/Scripts/python.exe -m alembic downgrade 0001_initial_schema
```

Таблица `knowledge_articles` из `0001` **преобразована**, а не продублирована:
это был тот же документ без области действия, хешей и сроков. Внешний ключ
`employee_questions.answer_source_article_id` переименован в `answer_source_id`
и перецелен на `knowledge_sources`.

**Индексы — HNSW, не ivfflat.** ivfflat строит списки по уже существующим
строкам и на пустой таблице бесполезен, а стартуем мы именно с пустой базы.

---

## 7. Кэш и воркер

**Кэш** — `CacheService` с реализацией в памяти. Ключ включает: хеш
нормализованного вопроса, язык, офис, регион, отпечаток области, **ревизию базы
знаний**, версию промпта и имя модели. Инвалидация происходит за счёт роста
ревизии — ключи не удаляются, они просто перестают адресоваться.

Redis не разворачивается: интерфейс готов, подключение — одна ветка в
`build_cache`. `AI_CACHE_BACKEND=redis` сейчас даёт явную ошибку, а не молчаливый
откат на память: молчаливый откат в проде означал бы кэш, невидимый соседним
воркерам, о чём никто бы не узнал.

**Персональные ответы не кэшируются никогда** — часы, отметки, больничные
и отпуска меняются в течение дня.

**Воркер** — очередь без брокера, поверх `knowledge_index_jobs`:

```bash
PYTHONPATH=. .venv/Scripts/python.exe -m src.modules.ai_assistant.worker
PYTHONPATH=. .venv/Scripts/python.exe -m src.modules.ai_assistant.worker --loop 10
```

Задача берётся через `SELECT ... FOR UPDATE SKIP LOCKED`, поэтому несколько
воркеров работают параллельно без внешней координации. При ошибке — до трёх
попыток с растущей паузой (1, 4, 9 минут), затем `FAILED` и статус `ERROR`
у версии; действующая версия при этом не выключается.

---

## 8. Настройка OpenAI API key

Ключ живёт **только** в `.env`, который в git не попадает (проверено
`git check-ignore`). В коде, логах и журнале `llm_query_logs` ключей нет:
всё, что пишется в журнал, проходит через `sanitize_for_log`.

```bash
cp .env.example .env
# заполнить OPENAI_API_KEY
```

Имена моделей задаются переменными и **нигде не зашиты в коде** — это
проверяется тестом `test_model_names_are_not_hardcoded_in_source`. Неизвестное
или недоступное имя даёт `ModelUnavailableError` с понятным текстом;
автоматического переключения на другую модель не происходит — молчаливая
подмена скрыла бы ошибку конфигурации.

---

## 9. Запуск

```bash
# 1. база с pgvector
docker compose -f infrastructure/docker/docker-compose.yml up -d

# 2. схема
cd apps/backend-api
PYTHONPATH=. .venv/Scripts/python.exe -m alembic upgrade head

# 3. справочники
PYTHONPATH=. .venv/Scripts/python.exe -m scripts.seed \
    --create-organization HUMOTECH --name "HUMOTECH" --timezone Asia/Dushanbe

# 4. включить модуль (только после утверждения HR-данных!)
#    AI_ASSISTANT_ENABLED=true и OPENAI_API_KEY в .env

# 5. воркер индексации
PYTHONPATH=. .venv/Scripts/python.exe -m src.modules.ai_assistant.worker --loop 10
```

---

## 10. Первый документ: от черновика до ответа

```python
from src.modules.ai_assistant.use_cases.crm import Actor, KnowledgeAdminUseCases

crm = KnowledgeAdminUseCases(session)
actor = Actor(user_id=hr_user_id, organization_id=org_id)

# 1. черновик (нужно разрешение knowledge.write)
source = crm.create_draft(
    actor,
    title="Порядок оформления отпуска",
    source_type="POLICY",
    language="ru",
    content="Заявление на отпуск подаётся не позднее чем за 14 дней...",
)

# 2. индексация (knowledge.index)
crm.start_indexing(actor, source.id)

# 3. воркер считает эмбеддинги; смотрим статус (knowledge.read)
crm.index_status(actor, source.id)

# 4. публикация (knowledge.publish) — атомарно, с ростом ревизии
crm.publish(actor, source.id)
```

Обновление версии: новый черновик с `parent_source_id` предыдущей версии →
индексация → публикация. Старая версия уходит в `ARCHIVED` в той же
транзакции, ревизия растёт, кэш обесценивается.

Архивация: `crm.archive(actor, source.id)` — тоже увеличивает ревизию.

---

## 11. Права

Новых механизмов прав не заводили — используется существующий каталог
`core/permissions/catalog.py` и области `user_role_scopes`:

| Разрешение | Действие | У кого есть |
|---|---|---|
| `knowledge.read` | история версий, preview, статус индексации | HR_ADMIN, REGIONAL_HR, OFFICE_ADMIN, MANAGER |
| `knowledge.write` | создание и правка черновика | HR_ADMIN |
| `knowledge.index` | запуск индексации | HR_ADMIN, TECH_ADMIN |
| `knowledge.publish` | публикация и архивация | HR_ADMIN |
| `questions.read` | список неизвестных вопросов | HR_ADMIN, REGIONAL_HR |
| `questions.answer` | назначение, создание FAQ, закрытие | HR_ADMIN, REGIONAL_HR |
| `ai.metrics.read` | журнал и метрики | HR_ADMIN, TECH_ADMIN |
| `audit.read` | audit trail изменений | TECH_ADMIN |

`SUPER_ADMIN` имеет все разрешения. Каждое административное действие пишется
в `audit_logs`.

**Важно:** область видимости сотрудника (`services/scoping.py`) и область
пользователя CRM (`core/permissions/scopes.py`) — **разные резолверы**. Первый
работает от `employee_id` через основное назначение, второй от `user_id`.
У сотрудника, который пишет боту, строки в `users` может не быть вообще.

---

## 12. Endpoints

HTTP-слоя в проекте пока нет (`src/api/routes` пуст), поэтому реализованы
use cases. Когда появится фреймворк, обвязка сведётся к тонким обработчикам:

| Метод | Use case | Для кого |
|---|---|---|
| `POST /ai/answer` | `AnswerUseCase.execute` | бот |
| `POST /ai/feedback` | `FeedbackUseCase.execute` | бот |
| `GET /ai/health` | `HealthUseCase.execute` | мониторинг |
| создание/правка черновика | `KnowledgeAdminUseCases.create_draft` / `update_draft` | CRM |
| запуск и статус индексации | `start_indexing` / `index_status` | CRM |
| preview поиска | `preview_search` | CRM |
| публикация, архивация | `publish` / `archive` | CRM |
| история версий, аудит | `version_history` / `audit_trail` | CRM |
| неизвестные вопросы | `list_unanswered` / `assign_question` / `create_faq_from_question` / `close_question` | CRM |

Формат ответа — `schemas.AnswerResponse`: `request_id`, `status`, `answer`,
`language`, `sources[{id,title,version,updated_at}]`, `retrieval_score`,
`score_band`, `used_model`, `cache_hit`, `fallback_used`, `knowledge_revision`.

Числовую уверенность у модели **не спрашиваем**: единственная оценка качества —
`retrieval_score`, посчитанный приложением.

---

## 13. Тесты

```bash
cd apps/backend-api

# модульные: без базы, без сети, без ключа
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest tests/unit -q

# интеграционные: нужен контейнер с pgvector
TEST_DATABASE_URL=postgresql+psycopg://humotech:humotech_local@127.0.0.1:5433/humotech_test \
  PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Модульные тесты запрещают сетевые соединения на уровне сокета: если провайдер
случайно окажется настоящим, тест упадёт, а не сходит в интернет.

Отдельного smoke-теста с реальным OpenAI сейчас нет — он появится, когда будет
ключ и ваше разрешение на реальные запросы.

---

## 14. Отключение AI

```
AI_ASSISTANT_ENABLED=false
```

Этого достаточно: фабрика провайдеров при выключенном рубильнике бросает
`ConfigurationError` и настоящий провайдер не создаётся. Воркер индексации
при `false` отказывается стартовать.

Данные при этом остаются на месте: база знаний, журнал и неотвеченные вопросы
никуда не деваются, модуль просто перестаёт обращаться к модели.

---

## 15. Что делать после получения данных от HR

1. Получить письменное разрешение на передачу корпоративных правил в OpenAI —
   это единственный вопрос, ответ на который нельзя вывести из репозитория.
2. Завести `OPENAI_API_KEY`, проверить `GET /ai/health`.
3. Загрузить первые документы черновиками, проиндексировать, опубликовать.
4. **Откалибровать пороги** `AI_EXACT_FAQ_THRESHOLD`, `AI_RAG_MIN_SCORE`,
   `AI_CONFLICT_SCORE_DELTA` на реальных вопросах: текущие значения —
   предварительные, угадать их заранее нельзя.
5. Подобрать веса гибридного поиска `VECTOR_WEIGHT` / `FTS_WEIGHT`
   в `services/retrieval.py`.
6. Реализовать `PersonalDataQueryService` поверх `attendance_sessions`,
   `employee_absences` и `leave_balances`.
7. Настроить правила приоритета для документов, которые пересекаются
   по смыслу, — иначе они будут уходить в `ESCALATED`.
8. Включить `AI_ASSISTANT_ENABLED=true`.
9. Наблюдать за `unanswered_questions`: самые частые вопросы там — готовый
   список того, чего не хватает в базе знаний.
