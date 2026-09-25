#!/usr/bin/env bash
# Проверка восстановления резервной копии HUMOTECH.
#
#   infrastructure/backup/restore-test.sh                 # последняя копия в $BACKUP_DIR
#   infrastructure/backup/restore-test.sh /path/to/<UTC>  # конкретная копия
#
# Рабочую базу скрипт не трогает вовсе. Восстановление идёт в ОТДЕЛЬНЫЙ
# одноразовый контейнер PostgreSQL:
#   * без опубликованных портов и во внутренней сети без выхода наружу;
#   * со случайным именем и случайным паролем, которые нигде не печатаются;
#   * контейнер, сеть и расшифрованные файлы удаляются при любом исходе.
#
# Что проверяется:
#   1. SHA256SUMS — копия не повреждена (от подмены целиком суммы без
#      подписи не защищают — это задача Object Lock и ключа «только запись»);
#   2. pg_restore завершается без ошибок (--exit-on-error);
#   3. число строк в КАЖДОЙ таблице равно записанному в manifest.tsv;
#   4. миграции по приложениям и расширения (vector, btree_gist) совпадают;
#   5. если задан BACKUP_VERIFY_APP_IMAGE — `manage.py migrate --check`
#      образом приложения: схема восстановленной базы соответствует коду;
#   6. архивы томов читаются, число файлов совпадает с манифестом.
#
# Код выхода 0 — копия пригодна. Любое расхождение — код 1 и строка
# «ПРОВЕРКА НЕ ПРОЙДЕНА» в журнале.
set -Eeuo pipefail
umask 077
LOG_TAG=restore-test
# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config
need docker

set_dir="${1:-}"
if [ -z "$set_dir" ]; then
    latest="$(ls -1 "$BACKUP_DIR" 2>/dev/null | grep -E '^20[0-9]{6}T[0-9]{6}Z$' | sort | tail -n 1 || true)"
    [ -n "$latest" ] || die "в $BACKUP_DIR нет ни одной копии"
    set_dir="$BACKUP_DIR/$latest"
fi
[ -f "$set_dir/manifest.tsv" ] || die "$set_dir: нет manifest.tsv"

suffix_rand="$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
container="humotech_restore_test_$suffix_rand"
network="humotech_restore_test_$suffix_rand"
db="restore_check"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/humotech-restore.XXXXXX")"
failures=0

cleanup() {
    # -v: у образа postgres анонимный том данных; без флага восстановленная
    # копия (с персональными данными) оставалась бы на диске после проверки.
    docker rm -f -v "$container" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    rm -rf "$tmp"
}
trap cleanup EXIT

fail() { log "РАСХОЖДЕНИЕ: $*"; failures=$((failures + 1)); }

# --- 1. Целостность ---------------------------------------------------------
log "копия: $set_dir"
(
    cd "$set_dir"
    while read -r sum name; do
        [ "$(sha256_file "$name")" = "$sum" ] || { echo "$name"; exit 1; }
    done < SHA256SUMS
) >/dev/null || die "контрольная сумма не сходится — копия повреждена"
log "контрольные суммы: в порядке"

db_file="$(ls -1 "$set_dir" | grep -E '^db\.dump(\.age|\.gpg)?$' | head -n 1)"
[ -n "$db_file" ] || die "в копии нет db.dump"
decrypt_file "$set_dir/$db_file" "$tmp/db.dump"

# --- 2. Одноразовый PostgreSQL ---------------------------------------------
pg_password="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
docker network create --internal "$network" >/dev/null
docker run -d --name "$container" --network "$network" \
    --label humotech.purpose=restore-test \
    -e POSTGRES_USER="$BACKUP_PG_USER" \
    -e POSTGRES_PASSWORD="$pg_password" \
    -e POSTGRES_DB="$db" \
    "$BACKUP_RESTORE_IMAGE" >/dev/null
# Готовность — по TCP, а не по сокету: во время первичной инициализации
# образ поднимает временный сервер только на сокете и потом перезапускает его.
for _ in $(seq 1 60); do
    docker exec "$container" pg_isready -h 127.0.0.1 -U "$BACKUP_PG_USER" -d "$db" >/dev/null 2>&1 && break
    sleep 1
done
docker exec "$container" pg_isready -h 127.0.0.1 -U "$BACKUP_PG_USER" -d "$db" >/dev/null \
    || die "временный PostgreSQL не поднялся"

docker exec -i "$container" sh -c 'cat > /tmp/db.dump' < "$tmp/db.dump"
rm -f "$tmp/db.dump"
started=$(date +%s)
docker exec "$container" pg_restore -U "$BACKUP_PG_USER" -d "$db" \
    --no-owner --no-acl --exit-on-error -j 2 /tmp/db.dump \
    || die "pg_restore завершился с ошибкой"
log "pg_restore: без ошибок за $(( $(date +%s) - started )) с"

# --- 3–4. Таблицы, миграции, расширения ------------------------------------
psql_q() { docker exec -i "$container" psql -X -q -At -F $'\t' -v ON_ERROR_STOP=1 -U "$BACKUP_PG_USER" -d "$db"; }

{
    echo "SELECT 'extension', extname, extversion FROM pg_extension ORDER BY extname;"
    echo "SELECT 'migrations', app, count(*) FROM django_migrations GROUP BY app ORDER BY app;"
    echo "SELECT format('SELECT %L, %L, count(*) FROM %I.%I', 'table', schemaname || '.' || tablename, schemaname, tablename) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY schemaname, tablename"
    echo '\gexec'
} | psql_q > "$tmp/restored.tsv"

grep -E '^(extension|migrations|table)'$'\t' "$set_dir/manifest.tsv" | sort > "$tmp/expected.sorted"
sort "$tmp/restored.tsv" > "$tmp/restored.sorted"
if ! diff -q "$tmp/expected.sorted" "$tmp/restored.sorted" >/dev/null; then
    # Печатаем имена таблиц и числа — не содержимое строк.
    while IFS= read -r line; do fail "$line"; done < <(diff "$tmp/expected.sorted" "$tmp/restored.sorted" | grep -E '^[<>]' | head -n 50)
fi
tables=$(grep -c '^table' "$tmp/restored.sorted" || true)
rows=$(awk -F'\t' '$1=="table"{s+=$3} END{print s+0}' "$tmp/restored.sorted")
migr=$(awk -F'\t' '$1=="migrations"{s+=$3} END{print s+0}' "$tmp/restored.sorted")
log "таблиц: $tables, строк всего: $rows, миграций: $migr"
for key in organizations employees attendance_events absence_requests files django_migrations; do
    n=$(awk -F'\t' -v t="public.$key" '$1=="table" && $2==t {print $3}' "$tmp/restored.sorted")
    log "  $key: ${n:-нет таблицы}"
done
grep -q $'^extension\tvector\t' "$tmp/restored.sorted" || fail "нет расширения vector"
[ "$migr" -gt 0 ] || fail "таблица django_migrations пуста"

# --- 5. Схема против кода ---------------------------------------------------
if [ -n "$BACKUP_VERIFY_APP_IMAGE" ]; then
    if docker run --rm --network "$network" \
        -e DJANGO_SETTINGS_MODULE="$BACKUP_VERIFY_SETTINGS" \
        -e DJANGO_DATABASE_URL="postgresql://$BACKUP_PG_USER:$pg_password@$container:5432/$db" \
        "$BACKUP_VERIFY_APP_IMAGE" python manage.py migrate --check --noinput >"$tmp/migrate.log" 2>&1; then
        log "migrate --check: непримененных миграций нет"
    else
        # Код 1 без трассировки — есть непримененные миграции; иначе образ
        # не смог подключиться или запуститься. Хвост журнала ниже различит.
        fail "migrate --check не прошёл на образе $BACKUP_VERIFY_APP_IMAGE"
        tail -n 5 "$tmp/migrate.log" | sed 's/^/    /' >&2
    fi
else
    log "migrate --check пропущен (BACKUP_VERIFY_APP_IMAGE не задан)"
fi

# --- 6. Тома -----------------------------------------------------------------
while IFS=$'\t' read -r kind vol expected; do
    [ "$kind" = "volume" ] || continue
    [ "$expected" = "missing" ] && { log "том $vol: при копировании отсутствовал"; continue; }
    arch="$(ls -1 "$set_dir" | grep -E "^volume-$vol\.tar\.gz(\.age|\.gpg)?$" | head -n 1)"
    [ -n "$arch" ] || { fail "нет архива тома $vol"; continue; }
    decrypt_file "$set_dir/$arch" "$tmp/vol.tar.gz"
    got=$(tar -tzf "$tmp/vol.tar.gz" | grep -vc '/$' || true)
    rm -f "$tmp/vol.tar.gz"
    [ "$got" = "$expected" ] && log "том $vol: файлов $got — совпадает" || fail "том $vol: файлов $got, ожидалось $expected"
done < "$set_dir/manifest.tsv"

if [ "$failures" -gt 0 ]; then
    log "ПРОВЕРКА НЕ ПРОЙДЕНА: расхождений $failures"
    exit 1
fi
log "ПРОВЕРКА ПРОЙДЕНА: копия $(basename "$set_dir") восстанавливается полностью"
