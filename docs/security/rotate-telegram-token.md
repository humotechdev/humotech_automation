# Ротация секретов: токен бота, секрет бот↔backend, ключи Django и QR

Документ для владельца системы. Значения секретов здесь не приводятся и
никуда не копируются: только имена переменных и файлы, где они живут.

## Почему это нужно сделать сейчас

Токен рабочего бота попал в репозиторий: в тестах
`apps/backend-api/django_tests/test_ai_safety.py` и
`apps/backend-api/tests/unit/test_ai_safety.py`, коммиты `81ada4b` и `59b3d27`.
Эти коммиты есть на GitHub (`origin/main`, `origin/hr-crm-reference-screens`).
Токен совпадает с текущими `TELEGRAM_BOT_TOKEN` (backend) и `BOT_TOKEN` (бот).
Проверять, работает ли он ещё, не нужно и нельзя: **он скомпрометирован**.

В рабочем дереве тесты уже переведены на фиктивный токен того же формата.
Но из истории git он никуда не делся — поэтому перевыпуск обязателен, а
чистка истории (раздел 7) полезна, но не заменяет его.

Что даёт токен бота постороннему:

- читать апдейты бота и отвечать от его имени (в том числе рассылать
  сотрудникам сообщения «от HR»);
- **подписывать `initData` Mini App от имени любого сотрудника**: backend
  проверяет подпись этим же токеном (`humotech/telegram/services.py`,
  `TELEGRAM["BOT_TOKEN"]`), а затем выдаёт Bearer на 12 часов. То есть
  утечка токена — это вход в Mini App под любым привязанным сотрудником.

## Где что живёт

| Переменная | Файл | Кто читает | Зачем |
|---|---|---|---|
| `BOT_TOKEN` | `apps/employee-telegram-bot/.env` | сервис `bot` | long polling и отправка сообщений |
| `TELEGRAM_BOT_TOKEN` | `apps/backend-api/.env` | `migrate`, `backend`, `export-worker`, `attendance-reminders`, `onboarding-reminders` | проверка подписи `initData` Mini App |
| `BACKEND_BOT_SECRET` | `apps/employee-telegram-bot/.env` | `bot` | заголовок `X-Bot-Token` к backend |
| `TELEGRAM_BOT_API_SECRET` | `apps/backend-api/.env` | backend (`humotech/telegram/auth.py`) | сверка того же заголовка |
| `DJANGO_SECRET_KEY` | `apps/backend-api/.env` | backend и воркеры | сессии CRM, Bearer Mini App (`django.core.signing`), ссылки сброса пароля |
| `QR_SIGNING_SECRET` | `apps/backend-api/.env` | backend | подпись вращающихся QR на экранах |
| пароль роли `humotech` | `DJANGO_DATABASE_URL` в `infrastructure/docker/.env` и `apps/backend-api/.env` | все сервисы backend | подключение к PostgreSQL |

`BOT_TOKEN` и `TELEGRAM_BOT_TOKEN` — **одно и то же значение** в двух файлах.
`BACKEND_BOT_SECRET` и `TELEGRAM_BOT_API_SECRET` — тоже одно значение.

Рабочий стек — проект compose `docker`, файлы
`infrastructure/docker/docker-compose.yml` + `infrastructure/docker/docker-compose.local.yml`.
Ниже для краткости:

```bash
DC="docker compose -p docker -f infrastructure/docker/docker-compose.yml -f infrastructure/docker/docker-compose.local.yml"
BACKEND_SERVICES="backend export-worker attendance-reminders onboarding-reminders"
```

> **Важно: `docker restart` / `docker compose restart` новые значения НЕ
> подхватывают.** `env_file` читается при создании контейнера. Нужен
> `up -d --force-recreate <сервисы>` — иначе старый токен продолжит работать
> в контейнере, а вы будете уверены, что сменили его.

Редактируйте `.env` так, чтобы значение не попадало в историю shell и на
экран: открывайте файл редактором (`nano`, `vim`), а не `echo ... >> .env`.

## 1. Токен бота (`BOT_TOKEN` = `TELEGRAM_BOT_TOKEN`)

1. В Telegram откройте **@BotFather** с аккаунта-владельца бота.
2. `/mybots` → выберите бота → **API Token** → **Revoke current token**
   (или команда `/revoke` и выбор бота). BotFather сразу выдаст новый токен.
   **С этой секунды старый токен мёртв**: бот начнёт получать 401 до
   перезапуска — это ожидаемо, делайте шаги 3–5 без пауз.
3. Впишите новый токен в **оба** файла:
   - `apps/employee-telegram-bot/.env` → `BOT_TOKEN=`
   - `apps/backend-api/.env` → `TELEGRAM_BOT_TOKEN=`
4. Пересоздайте сервисы:
   ```bash
   $DC up -d --force-recreate bot $BACKEND_SERVICES
   ```
   `migrate` отдельно пересоздавать не нужно: он запускается при следующем
   `up` и токен бота не использует.
5. Проверка — без вывода значений:
   ```bash
   # значения в двух файлах совпадают (печатаются только хэши, сравниваются глазами — или diff)
   # tr убирает кавычки, \r и перевод строки — иначе хэш файла не совпадёт с хэшем в контейнере
   grep -E '^BOT_TOKEN=' apps/employee-telegram-bot/.env | cut -d= -f2- | tr -d '\r\n"'"'" | sha256sum
   grep -E '^TELEGRAM_BOT_TOKEN=' apps/backend-api/.env | cut -d= -f2- | tr -d '\r\n"'"'" | sha256sum
   # в контейнерах — новое значение (сравнить с хэшами выше)
   docker exec humotech_bot sh -c 'printf %s "$BOT_TOKEN" | sha256sum'
   docker exec humotech_backend sh -c 'printf %s "$TELEGRAM_BOT_TOKEN" | sha256sum'
   # конфигурация бота (маскирует значения, в сеть не ходит)
   docker exec humotech_bot python -m src.check_config
   docker logs --since 5m humotech_bot 2>&1 | grep -iE 'unauthorized|401|error' || echo "ошибок нет"
   ```
   Затем вручную: написать боту `/start` — ответ пришёл; открыть Mini App
   кнопкой меню — открывается без ошибки входа.

Последствия:

- Mini App, открытые **до** смены, держат `initData`, подписанную старым
  токеном: обмен на внутренний токен не пройдёт, человеку нужно закрыть и
  заново открыть приложение.
- Уже выданные Bearer Mini App подписаны `DJANGO_SECRET_KEY`, а не токеном
  бота, — они доживают свои 12 часов. Если есть подозрение, что посторонний
  уже успел войти в Mini App под сотрудником, **ротируйте и
  `DJANGO_SECRET_KEY`** (раздел 3): только это обрывает выданные Bearer.
- Кнопка меню, ссылки `t.me/<бот>?start=...`, привязки сотрудников и chat id
  от смены токена не зависят — username бота прежний.
- Бот работает через long polling, вебхук переставлять не нужно.

## 2. Секрет бот↔backend (`BACKEND_BOT_SECRET` = `TELEGRAM_BOT_API_SECRET`)

Кто его знает, тот может от имени бота сообщить backend «этот Telegram ID —
вот этот человек» и работать через API бота как любой привязанный сотрудник.

1. Сгенерировать значение, не выводя его на экран, и вписать в оба файла:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))" > /tmp/new_bot_secret && chmod 600 /tmp/new_bot_secret
   # вставить содержимое в apps/backend-api/.env (TELEGRAM_BOT_API_SECRET=)
   # и в apps/employee-telegram-bot/.env (BACKEND_BOT_SECRET=) редактором
   shred -u /tmp/new_bot_secret
   ```
2. Пересоздать **одновременно** бот и backend-сервисы:
   ```bash
   $DC up -d --force-recreate bot $BACKEND_SERVICES
   ```
   Между пересозданием backend и бота запросы бота получают 401/403 —
   это секунды; очередь уведомлений (outbox) доставит пропущенное позже.
3. Проверка: хэши значений в двух файлах и в контейнерах совпадают (как в
   разделе 1, переменные `BACKEND_BOT_SECRET` / `TELEGRAM_BOT_API_SECRET`);
   `docker exec humotech_bot python -m src.check_config`; в логах backend
   нет всплеска 403 на `/api/v1/telegram/bot/*`; кнопка «Отметиться» в боте
   отвечает.

## 3. `DJANGO_SECRET_KEY`

Прежнее значение этого ключа попало в журнал сессии аудита 25.09.2026 —
**оно скомпрометировано**. Смена после утечки делается «жёстко»: старый
ключ перестаёт действовать сразу и больше не возвращается.

### Как это устроено в коде

`config/settings/production.py` читает две переменные, кроме самого ключа:

| Переменная | Что делает | При утечке |
|---|---|---|
| `DJANGO_SECRET_KEY_FALLBACKS` | Запасные ключи через запятую. Подписи, сделанные ими (сессии CRM, Bearer Mini App, ссылки сброса), **продолжают приниматься**. Нужна только для плановой смены без разлогинивания | **Оставить пустой.** Утёкший ключ сюда класть **нельзя**: подписи, сделанные им кем угодно, продолжили бы приниматься |
| `DJANGO_REVOKED_SECRET_KEY_SHA256` | SHA-256 отозванных ключей через запятую (хэши, не сами ключи). Если отозванный ключ окажется в `DJANGO_SECRET_KEY` или в `DJANGO_SECRET_KEY_FALLBACKS`, приложение **не запустится** (`ImproperlyConfigured`) | **Записать сюда хэш утёкшего ключа** — это страховка от того, что его по ошибке вернут в запасные |

Это поведение закреплено тестами
`apps/backend-api/django_tests/test_security_secret_rotation.py`: они
проверяют и сами настройки, и то, что этот документ им не противоречит.

### Последствия жёсткой смены

- **HR придётся войти в CRM заново**: все сессии CRM становятся
  недействительными (хэш сессии подписан ключом). Логин и пароль прежние.
- **Сотрудникам нужно закрыть и заново открыть Mini App**: все выданные
  Bearer Mini App (`humotech/telegram/tokens.py`, `django.core.signing`)
  перестают приниматься. Повторный вход проходит сам через `initData`,
  ничего вводить не нужно. Бот продолжает работать без действий
  сотрудников (он ходит к backend по отдельному секрету).
- Неиспользованные ссылки сброса пароля и прочие подписанные ссылки
  перестают работать.
- На QR, наклейки и привязки Telegram смена **не влияет** (у QR свой ключ,
  ссылки привязки хранятся хэшем в базе).

Предупредите HR заранее: «после HH:MM войдите в CRM заново; сотрудникам —
перезапустить Mini App, если откроется экран ошибки».

### Процедура (после утечки)

1. Посчитать SHA-256 **текущего** (утёкшего) ключа, не выводя сам ключ:
   ```bash
   grep -E '^DJANGO_SECRET_KEY=' apps/backend-api/.env | cut -d= -f2- | tr -d '\r\n"'"'" | sha256sum | cut -d' ' -f1
   ```
2. Редактором в `apps/backend-api/.env`:
   - `DJANGO_SECRET_KEY=` — новое значение
     (`python3 -c "import secrets; print(secrets.token_urlsafe(64))"`);
   - `DJANGO_SECRET_KEY_FALLBACKS=` — **пусто** (или строки нет вовсе);
   - `DJANGO_REVOKED_SECRET_KEY_SHA256=` — хэш из шага 1 (если строка уже
     есть — дописать через запятую).
3. `$DC up -d --force-recreate $BACKEND_SERVICES`.
4. Проверка:
   ```bash
   docker exec humotech_backend python manage.py check --deploy
   # запасных ключей нет:
   docker exec humotech_backend python -c "import django; django.setup(); from django.conf import settings; print(len(settings.SECRET_KEY_FALLBACKS))"   # 0
   ```
   CRM открывается страницей входа, вход работает; Mini App после
   переоткрытия пускает.

### Плановая смена (без утечки)

Если ключ меняется по регламенту, а не из-за утечки, можно не разлогинивать
людей: новый ключ — в `DJANGO_SECRET_KEY`, прежний — в
`DJANGO_SECRET_KEY_FALLBACKS`; через 12 часов (срок сессии CRM и Bearer
Mini App) прежний убрать из `DJANGO_SECRET_KEY_FALLBACKS` и пересоздать
сервисы ещё раз. **Для ключа, попавшего в журнал аудита, этот вариант не
применяется.**

## 4. `QR_SIGNING_SECRET`

Последствия смены:

- вращающиеся коды на экранах (`qr-display`) живут 30 секунд, экран сам
  запрашивает новый — через полминуты всё работает; отметки по коду,
  снятому за секунды до смены, отклоняются (человек сканирует ещё раз);
- **печатные наклейки (`qr_mode = STATIC`) не затрагиваются**: их секрет —
  случайная строка, хранится хэшем в базе и этим ключом не подписан;
- credential экранов (сопряжение `qr-display`) не затрагивается.

Процедура: новое значение (`secrets.token_urlsafe(48)`) →
`apps/backend-api/.env`, `QR_SIGNING_SECRET=` →
`$DC up -d --force-recreate $BACKEND_SERVICES` → проверка: на экране точки
через минуту новый код, тестовая отметка проходит.

## 5. Пароль базы данных

Пароль роли `humotech` из действующего `DJANGO_DATABASE_URL` (стек берёт
его из `infrastructure/docker/.env`: `environment:` в compose перекрывает
`env_file`) **совпадает со значением, которое лежит в истории git и в
текущих файлах репозитория** (`docker-compose.e2e.yml`,
`docs/development/isolated-checks.md`). Копия DSN в `apps/backend-api/.env`
тоже совпадает с закоммиченной в `.env.example`. При этом
`humotech_postgres` опубликован на `0.0.0.0:5433`. Значит, пароль надо
считать известным.

1. Сгенерировать пароль (только `[A-Za-z0-9]`, чтобы не экранировать в URL).
2. Сменить его в самой базе, не оставляя в истории shell:
   ```bash
   docker exec -it humotech_postgres psql -U humotech -d postgres
   -- внутри psql:
   \password humotech
   ```
3. Вписать новый пароль в `DJANGO_DATABASE_URL` в `infrastructure/docker/.env`
   и в `apps/backend-api/.env` (оба, если заданы).
4. `$DC up -d --force-recreate $BACKEND_SERVICES` и `$DC run --rm migrate`.
5. Проверка: `docker exec humotech_backend python manage.py showmigrations --plan | tail -1`
   отвечает, CRM открывается.

`POSTGRES_PASSWORD` в compose влияет только на **создание** пустого тома;
на существующей базе его менять бесполезно, но и оставлять там рабочий
пароль не стоит.

## 6. Прочее

- `NGROK_AUTHTOKEN` (`infrastructure/docker/.env`) в истории git не
  найден. Ротация — в панели ngrok, затем `$DC up -d --force-recreate ngrok`.
- `OPENAI_API_KEY`, `PEXELS_API_KEY` (`apps/backend-api/.env`) в истории не
  найдены. Ротация — в кабинете провайдера.
- `BOT_INTERNAL_TOKEN` бота имеет значение по умолчанию `change-me` и в
  коде бота не используется. Если внутренний порт бота когда-нибудь начнёт
  его проверять — значение нужно задать.

## 7. Очистка истории git (рекомендация владельцу, не выполнялась)

Удаление токена из истории **не отменяет** его утечку: клоны, форки,
кэши GitHub и логи CI уже могли его сохранить. Поэтому порядок такой:
сначала ротация (разделы 1–5), потом чистка — чтобы новый секрет никогда
не оказался рядом со старым и чтобы сканеры перестали поднимать тревогу.

Коммиты с токеном (`81ada4b`, `59b3d27`) уже на GitHub
(`origin/main`, `origin/hr-crm-reference-screens`). Значит, нужен
переписанный репозиторий и force-push всех веток.

1. Договориться со всеми, у кого есть клон: после чистки они **клонируют
   заново**, а не делают `pull` (иначе старая история вернётся push'ем).
   Все незакоммиченные изменения — закоммитить или сохранить заранее.
2. Сделать зеркальный клон и резервную копию:
   ```bash
   git clone --mirror https://github.com/humotechdev/humotech_automation.git humo-mirror.git
   cp -a humo-mirror.git humo-mirror-backup.git
   ```
3. Установить `git-filter-repo` (`pip install git-filter-repo`) и
   подготовить файл замен — **вне репозитория**, права 600:
   ```
   # replacements.txt: <старый токен>==>REDACTED_TELEGRAM_BOT_TOKEN
   ```
   (сам токен взять из текущего `.env` до его перевыпуска или из коммита
   `81ada4b`; в чат, тикет или документ его не вставлять).
4. Переписать историю:
   ```bash
   cd humo-mirror.git
   git filter-repo --replace-text ../replacements.txt
   git log -p --all | grep -c 'REDACTED_TELEGRAM_BOT_TOKEN'   # > 0
   # старого значения нет нигде (левая часть строки replacements.txt):
   git log -p --all | grep -cF "$(cut -d'=' -f1 ../replacements.txt | head -1)"   # 0
   ```
5. `git filter-repo` после перезаписи **удаляет remote `origin`** (защита
   от случайного push). Вернуть его и отправить ветки и теги:
   ```bash
   git remote add origin https://github.com/humotechdev/humotech_automation.git
   git push --force --all origin
   git push --force --tags origin
   ```
   `push --mirror` здесь не подходит: зеркальный клон содержит
   `refs/pull/*`, и GitHub отклоняет их запись («deny updating a hidden
   ref»). Если на ветках включена защита от force-push — временно снять и
   вернуть.
6. На GitHub: закрытые и открытые pull request'ы хранят старые коммиты
   (`refs/pull/*`) — их force-push не переписывает. Обратиться в поддержку
   GitHub с просьбой удалить закэшированные представления и ссылки на
   коммиты `81ada4b`, `59b3d27` (форма «Remove sensitive data»). Проверить
   форки репозитория.
7. Удалить `replacements.txt` (`shred -u`), всем участникам — свежий клон.

Альтернатива без переписывания истории — оставить её как есть после
ротации. Тогда токен в истории уже бесполезен, но сканеры секретов
(GitHub secret scanning и т. п.) продолжат сообщать о нём.

## 8. Как не допустить повторения

- В тестах — только фиктивные значения формата (`1234567890:AAFake...`).
- Включить GitHub secret scanning и push protection для репозитория.
- Перед коммитом — `gitleaks protect --staged` (pre-commit хук).
- Реальные `.env` — только на сервере, права 600, в `.gitignore`
  (уже так); в CI — через секреты CI, не файлами.
