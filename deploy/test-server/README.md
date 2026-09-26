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
# Полная копия (база + документы) тем же скриптом, что и по расписанию;
# код выхода не 0 — обновление не начинать (см. «Резервные копии»).
sudo BACKUP_CONFIG=/etc/humotech/backup.env /opt/humotech/infrastructure/backup/backup.sh
# Для быстрого отката схемы — ещё и отдельный дамп базы, вне репозитория:
sudo install -d -m 700 /var/backups/humotech-test
( umask 077; docker compose exec -T postgres pg_dump -U humotech -Fc humotech_django \
    > /var/backups/humotech-test/pre-deploy-$(date +%F-%H%M).dump )
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
       < /var/backups/humotech-test/pre-deploy-<дата>.dump
   sed -i "s/^HUMOTECH_TAG=.*/HUMOTECH_TAG=<прежний тег>/" .env
   docker compose up -d --no-build
   ```
   Обратные миграции Django (`migrate <app> <номер>`) — только если известно,
   что они обратимы; дамп надёжнее.

Тома `private_media` и `private_exports` при откате не трогаются.

## Резервные копии

Скрипты — `infrastructure/backup/` (подробно: `docs/security/backups.md`),
настройки этого стека — `deploy/test-server/backup.env.example`. Контейнер
базы и тома скрипт находит **по меткам Docker Compose** проекта
`humotech_test`, а не по угаданному имени. На стеке, поднятом этим
compose-файлом (Compose v2, проверено 25.09.2026), это:

| Что | Метки | Имя |
|---|---|---|
| база | `com.docker.compose.project=humotech_test`, `service=postgres` | `humotech_test-postgres-1`, база `humotech_django` |
| документы | `project=humotech_test`, `volume=private_media` | `humotech_test_private_media` |
| выгрузки | `project=humotech_test`, `volume=private_exports` | `humotech_test_private_exports` |

Копия объявляется готовой (каталог `/var/backups/humotech-test/<UTC>`, код
выхода 0) **только** если:
- найдены контейнер базы и **оба** тома;
- `pg_dump` прошёл и дамп читается `pg_restore`;
- каждый том заархивирован, а архив распакован и пересчитан по SHA-256;
- в томе документов файлов не меньше, чем живых строк в таблице `files`.

Иначе — код 1, в журнале `ОШИБКА: …`, незавершённый каталог удалён.
Сообщения различают «том … НЕ НАЙДЕН» и «том … существует, но ПУСТ». Пустым
может быть том выгрузок (они живут 72 часа), а том документов — только пока
база не ссылается ни на один файл.

### Настройка (один раз)

Репозиторий на сервере — в `/opt/humotech` (иначе поправьте `ExecStart` в
`infrastructure/backup/systemd/*.service`).

```sh
# 1. Ключ шифрования — на машине ВЛАДЕЛЬЦА, не на сервере.
age-keygen -o humotech-test-backup.key        # закрытый ключ хранить офлайн
grep 'public key:' humotech-test-backup.key   # строка age1... — открытый ключ

# 2. На сервере: age и настройки.
sudo apt-get install -y age
sudo install -d -m 700 /etc/humotech /var/backups/humotech-test
sudo install -m 600 /opt/humotech/deploy/test-server/backup.env.example /etc/humotech/backup.env
sudoedit /etc/humotech/backup.env
#   BACKUP_AGE_RECIPIENT=age1...                  (открытый ключ из шага 1)
#   BACKUP_VERIFY_APP_IMAGE=humotech/backend:<HUMOTECH_TAG из .env>

# 3. Скрипт видит именно этот стек:
docker ps --filter label=com.docker.compose.project=humotech_test \
          --filter label=com.docker.compose.service=postgres --format '{{.Names}}'
docker volume ls --filter label=com.docker.compose.project=humotech_test --format '{{.Name}}'

# 4. Первая копия вручную: код 0 и путь к каталогу копии последней строкой.
sudo BACKUP_CONFIG=/etc/humotech/backup.env /opt/humotech/infrastructure/backup/backup.sh; echo "код: $?"
sudo ls -l /var/backups/humotech-test/

# 5. Расписание: каждый день в 02:30 (±10 мин), пропущенный запуск — при старте.
sudo cp /opt/humotech/infrastructure/backup/systemd/humotech-backup.service \
        /opt/humotech/infrastructure/backup/systemd/humotech-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now humotech-backup.timer
systemctl list-timers humotech-backup.timer
sudo systemctl start humotech-backup.service
systemctl status humotech-backup.service --no-pager     # status=0/SUCCESS
journalctl -u humotech-backup -n 30 --no-pager           # «копия готова: …»
```

Сбой копии нужно видеть: `systemctl is-failed humotech-backup.service`
(`failed` — копии за эту ночь нет) — в мониторинг или через `OnFailure=`.
Копию вне сервера (rclone) включают по `docs/security/backups.md`.

### Проверка восстановления (после настройки и раз в месяц)

Нужен закрытый ключ, поэтому проверка идёт на машине владельца (или на
стенде), куда копия забирается с сервера. Там нужен Docker и образ backend
того же тега (`docker compose build migrate` из того же коммита).

```sh
last=$(ssh server 'sudo ls -1 /var/backups/humotech-test | grep -E "^20[0-9]{6}T" | tail -n 1')
ssh server "sudo tar -C /var/backups/humotech-test -cf - $last" | tar -xf -
printf '%s\n' "BACKUP_AGE_IDENTITY_FILE=$PWD/humotech-test-backup.key" \
              "BACKUP_VERIFY_APP_IMAGE=humotech/backend:<HUMOTECH_TAG>" > restore.env
BACKUP_CONFIG=restore.env infrastructure/backup/restore-test.sh "$PWD/$last"; echo "код: $?"
rm -rf "$last"
```

Код 0 и строка `ПРОВЕРКА ПРОЙДЕНА: … восстанавливается полностью (база и
файлы)` означают:
- база восстановлена в одноразовый PostgreSQL и сверена по всем таблицам;
- `migrate --check` прошёл;
- файлы обоих томов восстановлены во временные тома и совпали по SHA-256 с
  каждым файлом копии.

Рабочую базу и тома проверка не трогает.

### Восстановление стека из копии

Файлы копии расшифровываются на машине с ключом
(`age -d -i humotech-test-backup.key -o db.dump db.dump.age`, так же
`volume-*.tar.gz.age` и `volume-*.sha256.age`) и передаются на сервер.

```sh
cd /opt/humotech/deploy/test-server
docker compose stop backend bot export-worker attendance-reminders onboarding-reminders gateway
docker compose exec -T postgres dropdb -U humotech humotech_django
docker compose exec -T postgres createdb -U humotech -O humotech humotech_django
docker compose exec -T postgres pg_restore -U humotech -d humotech_django --no-owner --exit-on-error < db.dump
for v in private_media private_exports; do
  vol=$(docker volume ls -q --filter label=com.docker.compose.project=humotech_test \
                            --filter label=com.docker.compose.volume=$v)
  [ -n "$vol" ] || { echo "том $v не найден"; break; }
  # Том приводится ровно к состоянию копии: файлы, появившиеся после неё, удаляются.
  docker run --rm --network none -v "$vol:/dst" pgvector/pgvector:pg18 find /dst -mindepth 1 -delete
  docker run --rm -i --network none -v "$vol:/dst" pgvector/pgvector:pg18 tar -xzf - -C /dst < volume-$v.tar.gz
  if [ -s volume-$v.sha256 ]; then
    docker run --rm -i --network none -v "$vol:/dst:ro" pgvector/pgvector:pg18 \
        sh -c 'cd /dst && sha256sum --quiet -c - && echo "$0: файлы совпали"' "$v" < volume-$v.sha256
  else
    echo "$v: в копии том был пуст"
  fi
done
docker compose up -d
shred -u db.dump volume-*.tar.gz volume-*.sha256
```

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

- Резервная копия — раз в сутки (RPO ≤ 24 ч); непрерывной архивации WAL нет.
- Проверка восстановления по расписанию на сервере не запускается: она
  требует закрытого ключа, которого на сервере нет (см. «Проверка
  восстановления»).
- `onboarding-reminders` проходит цикл раз в час, поэтому его зависание
  healthcheck заметит примерно через 2 часа.
