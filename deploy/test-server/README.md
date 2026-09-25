# Тестовый сервер HUMOTECH

Конфигурация одного Linux-сервера с Docker: Caddy (HTTPS) → шлюз nginx
(Mini App + `/api/`) → Django, рядом CRM статикой, бот и рабочие процессы.
Локальный стенд (`infrastructure/docker`, ngrok, Vite dev) этим файлом не
затрагивается.

```
интернет ──443──▶ caddy ──┬─ API_DOMAIN ──────────────▶ gateway ──▶ backend ──▶ postgres
                          └─ CRM_DOMAIN ─ /api/* ─────▶ gateway        ▲            (сеть db,
                                        └─ остальное ─▶ crm            │             internal)
                                             bot ─────▶ gateway ───────┘
```

| Сеть | Кто | Наружу |
|---|---|---|
| `edge` (172.31.250.0/24) | caddy (фиксированный 172.31.250.10), gateway, crm | порты 80/443 только у caddy |
| `app` | gateway, backend, bot | исходящие есть (боту нужен Telegram) |
| `db` (`internal: true`) | postgres, backend, migrate, рабочие процессы | нет маршрута наружу, портов нет |

## Первый запуск

1. DNS: `API_DOMAIN` и `CRM_DOMAIN` — A-записи на сервер. Открыты 80 и 443/tcp
   (80 нужен Let's Encrypt для HTTP-01 и для редиректа на HTTPS).
2. Настройки:
   ```sh
   cd deploy/test-server
   cp .env.example .env && chmod 600 .env
   # заполнить все пустые значения; секреты — secrets.token_urlsafe(48)
   docker compose config --quiet   # упадёт с именем незаполненной переменной
   ```
3. Сборка и запуск. Тег образов — только в `.env` (`HUMOTECH_TAG`), не
   `export` в оболочке: иначе `docker compose up -d <сервис>` из новой
   оболочки собрал бы образы под другим тегом и пересоздал backend.
   ```sh
   sed -i "s/^HUMOTECH_TAG=.*/HUMOTECH_TAG=$(git rev-parse --short HEAD)/" .env
   docker compose build
   docker compose up -d
   docker compose ps          # все сервисы healthy, migrate — exited (0)
   ```
4. Учётная запись HR: `docker compose exec backend python manage.py createsuperuser`
   (или команда посева организации — см. `apps/backend-api/README.md`).

## Обновление

```sh
git pull
grep '^HUMOTECH_TAG=' .env            # запомнить прежний тег — он нужен для отката
sed -i "s/^HUMOTECH_TAG=.*/HUMOTECH_TAG=$(git rev-parse --short HEAD)/" .env
# Резервная копия ДО миграций — откатить схему без неё нельзя.
# Вне каталога репозитория и только для владельца: в дампе все кадровые данные.
sudo install -d -m 700 /var/backups/humotech
( umask 077; docker compose exec -T postgres pg_dump -U humotech -Fc humotech_django \
    > /var/backups/humotech/pre-deploy-$(date +%F-%H%M).dump )
docker compose build
docker compose up -d        # migrate отработает первым, backend ждёт его
```

Прежние образы остаются с прежним тегом (`humotech/*:<старый тег>`), поэтому
откат не требует пересборки.

## Откат

1. Код без изменений схемы:
   ```sh
   sed -i "s/^HUMOTECH_TAG=.*/HUMOTECH_TAG=<прежний тег>/" .env
   docker compose up -d --no-build
   ```
2. Если новая версия применила миграции: сначала остановить приложение,
   восстановить базу из дампа, сделанного перед обновлением, затем п. 1:
   ```sh
   docker compose stop backend bot export-worker attendance-reminders onboarding-reminders gateway
   docker compose exec -T postgres pg_restore -U humotech -d humotech_django --clean --if-exists \
       < /var/backups/humotech/pre-deploy-<дата>.dump
   sed -i "s/^HUMOTECH_TAG=.*/HUMOTECH_TAG=<прежний тег>/" .env
   docker compose up -d --no-build
   ```
   Обратные миграции Django (`migrate <app> <номер>`) — только если известно,
   что они обратимы; дамп надёжнее.

Тома `private_media` и `private_exports` при откате не трогаются.

## Проверки после деплоя

```sh
# 1. Все сервисы healthy
docker compose ps

# 2. TLS и заголовки
curl -sI https://$API_DOMAIN/ | grep -Ei 'strict-transport|content-security|x-content-type'
curl -sI https://$CRM_DOMAIN/ | grep -Ei 'strict-transport|content-security|x-frame-options'

# 3. Живость и готовность backend через весь путь
curl -fsS https://$API_DOMAIN/health/ready

# 4. Django Admin закрыт снаружи (ожидается 403; 200 — только с адресов GATEWAY_ADMIN_ALLOW)
curl -s -o /dev/null -w '%{http_code}\n' https://$API_DOMAIN/admin/

# 5. Подмена X-Forwarded-For не проходит: в журнале шлюза первым полем стоит
#    ваш настоящий адрес, а не 6.6.6.6 и не адрес Caddy (172.31.250.10)
curl -s -o /dev/null -H 'X-Forwarded-For: 6.6.6.6' https://$API_DOMAIN/health/ready
docker compose logs --tail=3 gateway

# 6. База не опубликована
docker compose port postgres 5432 || echo "не опубликована — так и должно быть"
ss -ltnp | grep -E ':5432|:5433' || echo "на хосте не слушается"

# 7. CRM: вход по паролю, открыть «Администрирование»; Mini App — кнопкой в боте.
```

## Django Admin

По умолчанию закрыт (403 на шлюзе). Открыть для своего адреса:

```sh
echo 'GATEWAY_ADMIN_ALLOW=203.0.113.10/32' >> .env
docker compose up -d gateway
```

Адрес сравнивается с настоящим адресом клиента (Caddy → шлюз), подделать его
заголовком нельзя. На `CRM_DOMAIN` админка недоступна вовсе: туда на шлюз идёт
только `/api/*`.

## Что где

| Файл | Назначение |
|---|---|
| `docker-compose.yml` | сервисы, сети, тома, healthchecks |
| `Caddyfile` | домены, TLS, HSTS, лимит тела, таймауты |
| `crm/nginx.conf` | раздача CRM, CSP и заголовки |
| `initdb/01_databases.sql` | база `humotech_django` и расширения при создании тома |
| `../../infrastructure/docker/gateway/` | общий со стендом конфиг шлюза (realip, `/admin/`, CSP Mini App) |
| `../../apps/hr-crm/Dockerfile` | сборка CRM `vite build` |

## Ограничения

- Резервное копирование по расписанию здесь не настроено — см.
  `infrastructure/backup/`.
- `attendance-reminders` и `onboarding-reminders` проверяются по доступности
  базы: собственного признака жизни (как у `export-worker`) у них пока нет,
  и зависший, но живой цикл healthcheck не заметит.
- Счётчики ограничения частоты — в памяти процесса (или в базе, где это
  сделано), общего Redis нет.
