# Linux-стенд для проверки бэкапа

Изолированный контейнер Debian bookworm с `age`, `rclone` и systemd — чтобы
проверить скрипты бэкапа так, как они работают на сервере, в том числе с
машины разработчика на Windows. Своего Docker внутри нет: скрипты работают
с Docker хоста через смонтированный сокет и создают только одноразовые
контейнеры и тома с вымышленными данными. Приложение, бот и Caddy не
запускаются; внешнее хранилище rclone — локальный каталог внутри стенда.

## Сборка

```sh
docker build -t humotech-backup-lab infrastructure/backup/tests/lab
```

## Тесты скриптов (`test-backup.sh`: age, rclone, все случаи отказа)

```sh
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$PWD":/opt/humotech:ro --entrypoint bash humotech-backup-lab \
    /opt/humotech/infrastructure/backup/tests/test-backup.sh
```

Итог — строка `Итог: N PASS, 0 FAIL, 0 SKIP`; код выхода не 0 при любом FAIL.
В Git Bash на Windows перед командой нужен `MSYS_NO_PATHCONV=1`, а вместо
`$PWD` — путь вида `D:/HUMO`.

## Команды README тестового сервера под systemd

```sh
docker run -d --name hbt-systemd --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$PWD":/opt/humotech:ro humotech-backup-lab
docker exec hbt-systemd systemctl is-system-running    # running
```

Дальше внутри (`docker exec -it hbt-systemd bash`):

1. создать вымышленный стек с метками проекта `humotech_test`:
   - PostgreSQL `humotech_test-postgres-1`;
   - тома `humotech_test_private_media` и `humotech_test_private_exports`;
   - таблица `files` с настоящими SHA-256 файлов.

   Метки — `com.docker.compose.project=humotech_test` и `.service`/`.volume`;
2. выполнить раздел «Резервные копии» из `deploy/test-server/README.md`:
   настройку, первую копию, таймер;
3. проверить ошибки: удалить том → `systemctl status` `status=1`; сделать
   недоступным `BACKUP_RCLONE_REMOTE` → `status=75`;
4. проверить восстановление: `humotech-restore-test.service` и команды
   README.

Убрать за собой:

```sh
docker rm -f -v hbt-systemd humotech_test-postgres-1
docker volume rm humotech_test_private_media humotech_test_private_exports
```

`docker.service` в стенде — заглушка: демон здесь чужой (хоста), а
unit-файлы бэкапа требуют `docker.service`. На настоящем сервере это
обычный Docker.
