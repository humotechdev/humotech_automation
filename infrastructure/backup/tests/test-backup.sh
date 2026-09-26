#!/usr/bin/env bash
# Проверки backup.sh и restore-test.sh на изолированном окружении.
#
#   infrastructure/backup/tests/test-backup.sh
#
# Поднимает одноразовый PostgreSQL и тома Docker с теми же метками, что
# ставит Docker Compose (com.docker.compose.project/.service/.volume), под
# случайным именем проекта. Данные вымышленные. Рабочие контейнеры и тома
# не читаются и не трогаются; всё созданное удаляется при любом исходе.
#
# Нужны: docker, bash, образ pgvector/pgvector:pg18 (скачается при
# отсутствии). Шифрование в проверках — none: age/gpg проверяются отдельно
# (docs/security/backups.md).
set -Eeuo pipefail
export MSYS_NO_PATHCONV=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BK="$(cd "$HERE/.." && pwd)"
IMAGE="${BACKUP_TEST_IMAGE:-pgvector/pgvector:pg18}"

rand="$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
P="hbt_$rand"                         # имя «проекта compose»
PG="${P}-postgres-1"
MEDIA="${P}_private_media" EXPORTS="${P}_private_exports"
NOTAR="hbt-notar:$rand" BADTAR="hbt-badtar:$rand"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/hbt.XXXXXX")"
passed=0 failed=0

cleanup() {
    docker rm -f -v "$PG" >/dev/null 2>&1 || true
    docker volume rm -f "$MEDIA" "$EXPORTS" >/dev/null 2>&1 || true
    docker rmi -f "$NOTAR" "$BADTAR" >/dev/null 2>&1 || true
    # тома, которые мог оставить прерванный restore-test
    docker volume ls -q --filter label=humotech.purpose=restore-test | grep "restore_test" | xargs -r docker volume rm -f >/dev/null 2>&1 || true
    rm -rf "$tmp"
}
trap cleanup EXIT

say() { printf '\n=== %s\n' "$*"; }
ok() { passed=$((passed + 1)); printf '  PASS  %s\n' "$*"; }
bad() { failed=$((failed + 1)); printf '  FAIL  %s\n' "$*"; }

# --- окружение -----------------------------------------------------------------
say "окружение: проект $P"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull -q "$IMAGE" >/dev/null
pgpass="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
docker run -d --name "$PG" --network none \
    --label com.docker.compose.project="$P" --label com.docker.compose.service=postgres \
    -e POSTGRES_USER=humotech -e POSTGRES_PASSWORD="$pgpass" -e POSTGRES_DB=humotech_django \
    "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
    docker exec "$PG" pg_isready -h 127.0.0.1 -U humotech -d humotech_django >/dev/null 2>&1 && break
    sleep 1
done
docker exec -i "$PG" psql -X -q -v ON_ERROR_STOP=1 -U humotech -d humotech_django >/dev/null <<'SQL'
CREATE EXTENSION vector;
CREATE EXTENSION btree_gist;
CREATE TABLE django_migrations (id serial PRIMARY KEY, app text, name text, applied timestamptz DEFAULT now());
INSERT INTO django_migrations (app, name) SELECT 'employees', format('%04s_fake', g) FROM generate_series(1, 9) g;
INSERT INTO django_migrations (app, name) SELECT 'files', format('%04s_fake', g) FROM generate_series(1, 2) g;
CREATE TABLE employees (id serial PRIMARY KEY, full_name text, embedding vector(3));
INSERT INTO employees (full_name, embedding) SELECT 'Вымышленный Сотрудник ' || g, '[1,2,3]' FROM generate_series(1, 250) g;
CREATE TABLE files (id serial PRIMARY KEY, storage_key text NOT NULL, deleted_at timestamptz);
INSERT INTO files (storage_key) VALUES ('absences/a.pdf'), ('absences/b.pdf'), ('employees/c.png');
INSERT INTO files (storage_key, deleted_at) VALUES ('absences/old.pdf', now());
SQL
for v in "$MEDIA:private_media" "$EXPORTS:private_exports"; do
    docker volume create --label com.docker.compose.project="$P" \
        --label com.docker.compose.volume="${v#*:}" "${v%%:*}" >/dev/null
done
# 4 файла: вложенный каталог, кириллица и пробел в имени, двоичное содержимое.
docker run --rm --network none -v "$MEDIA:/m" "$IMAGE" sh -c '
    mkdir -p /m/absences/2026 /m/employees
    printf "%%PDF-1.4 fake a\n" > /m/absences/2026/a.pdf
    printf "%%PDF-1.4 fake b\n" > "/m/absences/2026/справка №2.pdf"
    head -c 4096 /dev/urandom > /m/employees/c.png
    printf "fake\n" > /m/employees/notes.txt'
# Два образа-помощника с испорченным tar (собираются из того же образа,
# без сети): один завершается ошибкой, другой «успешно» выдаёт мусор.
build_helper() {  # build_helper <тег> <тело скрипта /usr/local/bin/tar>
    local ctx="$tmp/ctx-${1%%:*}"
    mkdir -p "$ctx"
    printf '#!/bin/sh\n%s\n' "$2" > "$ctx/tar"
    printf 'FROM %s\nCOPY tar /usr/local/bin/tar\nRUN chmod 755 /usr/local/bin/tar\n' "$IMAGE" > "$ctx/Dockerfile"
    # Git Bash на Windows: docker.exe не понимает путь /tmp/... — нужен Windows-путь.
    docker build -q -t "$1" "$(cygpath -w "$ctx" 2>/dev/null || echo "$ctx")" >/dev/null
}
build_helper "$NOTAR" 'echo "simulated tar failure" >&2; exit 2'
build_helper "$BADTAR" 'case "$*" in *-czf*) echo not-a-gzip-archive; exit 0;; esac; exec /usr/bin/tar "$@"'

# --- запуск ---------------------------------------------------------------------
# run_backup <имя случая> [NAME=VALUE ...] — пишет конфиг, запускает backup.sh.
# Код выхода — в $rc, журнал — в $out, каталог копий — $bdir.
run_backup() {
    local name="$1"; shift
    bdir="$tmp/$name"
    local cfg="$tmp/$name.env"
    {
        echo "BACKUP_COMPOSE_PROJECT=$P"
        echo "BACKUP_DIR=$bdir"
        echo "BACKUP_ENCRYPTION=none"
        echo "BACKUP_REMOTE_ENABLED=false"
        echo "BACKUP_ALLOW_EMPTY_VOLUMES=private_exports"
        echo "BACKUP_HELPER_IMAGE=$IMAGE"
        echo "BACKUP_RESTORE_IMAGE=$IMAGE"
        echo "BACKUP_VERIFY_APP_IMAGE="
        local kv; for kv in "$@"; do printf '%s="%s"
' "${kv%%=*}" "${kv#*=}"; done
    } > "$cfg"
    set +e
    out="$(BACKUP_CONFIG="$cfg" bash "$BK/backup.sh" 2>&1)"; rc=$?
    set -e
}
final_sets() { ls -1 "$bdir" 2>/dev/null | grep -cE '^20[0-9]{6}T[0-9]{6}Z$' || true; }
partials() { ls -1A "$bdir" 2>/dev/null | grep -c '^\.partial-' || true; }

# expect_refused <случай> <шаблон сообщения> [<шаблон, которого быть НЕ должно>]
expect_refused() {
    local what="$1" pattern="$2" not="${3:-}"
    if [ "$rc" -ne 0 ] && grep -q -- "$pattern" <<<"$out" && [ "$(final_sets)" = 0 ] && [ "$(partials)" = 0 ] \
        && { [ -z "$not" ] || ! grep -q -- "$not" <<<"$out"; }; then
        ok "$what: отказ ($pattern), готового каталога нет"
    else
        bad "$what: rc=$rc, готовых каталогов $(final_sets), .partial $(partials)"
        printf '%s\n' "$out" | tail -n 5 | sed 's/^/        /'
    fi
}

# --- 1. всё на месте → копия + восстановление базы и файлов -----------------------
say "1. обязательные тома есть (один пустой — разрешён)"
run_backup ok
set_dir="$bdir/$(ls -1 "$bdir" | grep -E '^20[0-9]{6}T' | head -n 1)"
m="$set_dir/manifest.tsv"
if [ "$rc" -eq 0 ] && [ "$(final_sets)" = 1 ] \
    && grep -q $'^volume\tprivate_media\t'"$MEDIA"$'\t4$' "$m" \
    && grep -q $'^volume\tprivate_exports\t'"$EXPORTS"$'\t0$' "$m" \
    && grep -q $'^meta\tfiles_live\t3$' "$m" && grep -q $'^meta\tstatus\tcomplete$' "$m" \
    && grep -q "существует, пуст (разрешено" <<<"$out"; then
    ok "копия создана; тома найдены по меткам: $MEDIA (4 файла), $EXPORTS (пуст, разрешено)"
else
    bad "копия: rc=$rc"; printf '%s\n' "$out" | tail -n 8 | sed 's/^/        /'
fi
set +e
rout="$(BACKUP_CONFIG="$tmp/ok.env" bash "$BK/restore-test.sh" "$set_dir" 2>&1)"; rrc=$?
set -e
if [ "$rrc" -eq 0 ] && grep -q "ПРОВЕРКА ПРОЙДЕНА" <<<"$rout" \
    && grep -q "том private_media ($MEDIA): восстановлено файлов 4, SHA-256 всех совпали" <<<"$rout" \
    && grep -q "таблиц: 3, строк всего: 265" <<<"$rout"; then
    ok "восстановление: база (3 таблицы, 265 строк) и 4 файла побайтно"
else
    bad "восстановление: rc=$rrc"; printf '%s\n' "$rout" | tail -n 10 | sed 's/^/        /'
fi

# --- 2. восстановленный файл отличается от снятого → проверка ловит -----------------
say "2. подменённый файл в архиве при верных SHA256SUMS"
evil="$tmp/evil"; cp -r "$set_dir" "$evil"
docker run --rm -i --network none "$IMAGE" sh -c \
    'set -e; mkdir /x; tar -xzf - -C /x; echo tampered >> /x/employees/notes.txt; tar -C /x -czf - .' \
    < "$set_dir/volume-private_media.tar.gz" > "$evil/volume-private_media.tar.gz"
( cd "$evil" && for f in db.dump volume-*.tar.gz volume-*.sha256 manifest.tsv; do
      printf '%s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$f"; done > SHA256SUMS )
set +e
rout="$(BACKUP_CONFIG="$tmp/ok.env" bash "$BK/restore-test.sh" "$evil" 2>&1)"; rrc=$?
set -e
if [ "$rrc" -ne 0 ] && grep -q "не совпадают с контрольными суммами" <<<"$rout"; then
    ok "restore-test: расхождение файла найдено, ПРОВЕРКА НЕ ПРОЙДЕНА"
else
    bad "restore-test не заметил подмену: rc=$rrc"
fi

# --- 3. обязательный том не найден ----------------------------------------------
say "3. отсутствующий том"
run_backup missing "BACKUP_COMPOSE_VOLUMES=private_media private_exports private_scans"
expect_refused "том не найден" "том private_scans проекта $P НЕ НАЙДЕН" "ПУСТ"

# --- 4. пустой том, которому быть пустым нельзя ---------------------------------
say "4. пустой том без разрешения"
run_backup empty "BACKUP_ALLOW_EMPTY_VOLUMES="
expect_refused "том пуст" "том private_exports ($EXPORTS) существует, но ПУСТ" "НЕ НАЙДЕН"

# --- 5. документы, на которые ссылается база, не попали бы в копию ----------------
say "5. в базе живые файлы, том документов пуст"
run_backup media_empty "BACKUP_COMPOSE_VOLUMES=private_exports" "BACKUP_MEDIA_VOLUME=private_exports"
expect_refused "база ссылается на файлы" "в базе 3 живых файлов, а в томе private_exports только 0"

say "5б. том документов пуст «по ошибке», база ссылается на файлы"
docker run --rm --network none -v "$MEDIA:/m" "$IMAGE" find /m -mindepth 1 -delete
run_backup media_wiped "BACKUP_ALLOW_EMPTY_VOLUMES=private_exports private_media"
expect_refused "том документов опустошён" "в томе private_media только 0"
run_backup media_wiped2
expect_refused "том документов опустошён (без разрешения)" "том документов private_media ($MEDIA) существует, но ПУСТ, а база ссылается на 3 живых файлов"
# вернуть файлы из копии случая 1 — так же, как при настоящем восстановлении
docker run --rm -i --network none -v "$MEDIA:/dst" "$IMAGE" tar -xzf - -C /dst < "$set_dir/volume-private_media.tar.gz"
docker run --rm -i --network none -v "$MEDIA:/dst:ro" "$IMAGE" sh -c 'cd /dst && sha256sum --quiet -c -'     < "$set_dir/volume-private_media.sha256" && ok "файлы тома документов возвращены из копии, SHA-256 совпали"     || bad "возврат файлов из копии"

# --- 6. tar завершился с ошибкой ---------------------------------------------------
say "6. ошибка архивации"
run_backup tar_fails "BACKUP_HELPER_IMAGE=$NOTAR"
expect_refused "tar с ошибкой" "архивация завершилась с ошибкой"

# --- 7. tar «успешен», но архив не читается --------------------------------------
say "7. испорченный архив при нулевом коде tar"
run_backup tar_garbage "BACKUP_HELPER_IMAGE=$BADTAR"
expect_refused "мусор вместо архива" "архив не распаковывается"

# --- 8. явные имена (режим локального стенда) ---------------------------------
say "8. режим явных имён"
run_backup explicit "BACKUP_COMPOSE_PROJECT=" "BACKUP_PG_CONTAINER=$PG" "BACKUP_VOLUMES=$MEDIA $EXPORTS" \
    "BACKUP_ALLOW_EMPTY_VOLUMES=$EXPORTS"
[ "$rc" -eq 0 ] && [ "$(final_sets)" = 1 ] && ok "явные имена: копия создана" \
    || { bad "явные имена: rc=$rc"; printf '%s\n' "$out" | tail -n 5 | sed 's/^/        /'; }
run_backup explicit_missing "BACKUP_COMPOSE_PROJECT=" "BACKUP_PG_CONTAINER=$PG" "BACKUP_VOLUMES=$MEDIA ${P}_nope"
expect_refused "явное имя тома не найдено" "том ${P}_nope НЕ НАЙДЕН"

# --- 9. новый сервер: том документов пуст, и база на файлы не ссылается ------------
say "9. пустой том документов при нуле живых файлов в базе"
docker exec "$PG" psql -X -q -U humotech -d humotech_django -c "UPDATE files SET deleted_at = now()" >/dev/null
run_backup fresh "BACKUP_COMPOSE_VOLUMES=private_exports" "BACKUP_MEDIA_VOLUME=private_exports" "BACKUP_ALLOW_EMPTY_VOLUMES="
if [ "$rc" -eq 0 ] && [ "$(final_sets)" = 1 ] && grep -q "в базе нет ни одного живого файла — допустимо" <<<"$out"; then
    ok "пустой том документов при нуле живых файлов: копия создана"
else
    bad "новый сервер: rc=$rc"; printf '%s
' "$out" | tail -n 5 | sed 's/^/        /'
fi

# --- 10. база остановлена ----------------------------------------------------------
say "10. контейнер базы остановлен"
docker stop -t 5 "$PG" >/dev/null
run_backup pg_stopped
expect_refused "база остановлена" "есть, но не запущен"

printf '\nИтог: %d PASS, %d FAIL\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
