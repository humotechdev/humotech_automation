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
#   * контейнер, сеть, временные тома и расшифрованные файлы удаляются при
#     любом исходе.
#
# Что проверяется:
#   1. SHA256SUMS — копия не повреждена (от подмены целиком суммы без
#      подписи не защищают — это задача Object Lock и ключа «только запись»);
#   2. расшифровка — неверный ключ или испорченный файл дают явную ошибку;
#   3. pg_restore завершается без ошибок (--exit-on-error);
#   4. число строк в КАЖДОЙ таблице, миграции и расширения совпадают с
#      manifest.tsv;
#   5. если задан BACKUP_VERIFY_APP_IMAGE — `manage.py migrate --check`
#      образом приложения: схема восстановленной базы соответствует коду;
#   6. файлы томов ВОССТАНАВЛИВАЮТСЯ во временные тома Docker, и каждый
#      файл сверяется по SHA-256 со списком, снятым при копировании;
#   7. документы: живые строки files ВОССТАНОВЛЕННОЙ базы совпадают со
#      списком документов копии, и каждый такой документ есть в
#      восстановленном томе с тем же SHA-256, что files.checksum_sha256.
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
grep -q $'^meta\tstatus\tcomplete$' "$set_dir/manifest.tsv" \
    || die "$set_dir: в манифесте нет отметки complete — копия незавершённая или старого формата"

suffix_rand="$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
container="humotech_restore_test_$suffix_rand"
network="humotech_restore_test_$suffix_rand"
db="restore_check"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/humotech-restore.XXXXXX")"
failures=0

restore_volumes=()
cleanup() {
    # -v: у образа postgres анонимный том данных; без флага восстановленная
    # копия (с персональными данными) оставалась бы на диске после проверки.
    docker rm -f -v "$container" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    local v
    for v in "${restore_volumes[@]}"; do docker volume rm -f "$v" >/dev/null 2>&1 || true; done
    rm -rf "$tmp"
}
trap cleanup EXIT

fail() { log "РАСХОЖДЕНИЕ: $*"; failures=$((failures + 1)); }
# find_part <шаблон имени без суффикса шифрования> — имя файла копии или пусто.
find_part() { ls -1 "$set_dir" | grep -E "^$1(\.age|\.gpg)?$" | head -n 1 || true; }
helper() { docker run --rm --network none "$@"; }

# --- 1. Целостность ---------------------------------------------------------
log "копия: $set_dir"
(
    cd "$set_dir"
    while read -r sum name; do
        [ "$(sha256_file "$name")" = "$sum" ] || { echo "$name"; exit 1; }
    done < SHA256SUMS
) >/dev/null || die "контрольная сумма не сходится — копия повреждена"
log "контрольные суммы: в порядке"

# --- 2. Расшифровка ----------------------------------------------------------
db_file="$(find_part 'db\.dump')"
[ -n "$db_file" ] || die "в копии нет db.dump"
decrypt_file "$set_dir/$db_file" "$tmp/db.dump"
docs_file="$(find_part 'documents\.tsv')"
[ -n "$docs_file" ] || die "в копии нет списка документов (documents.tsv)"
decrypt_file "$set_dir/$docs_file" "$tmp/documents.tsv"
media_key="$(awk -F'\t' '$1 == "meta" && $2 == "media_volume" {print $3}' "$set_dir/manifest.tsv")"
[ -n "$media_key" ] || die "в манифесте не указан том документов"
docs_expected="$(awk -F'\t' '$1 == "meta" && $2 == "documents" {print $3}' "$set_dir/manifest.tsv")"
[ "$(grep -c . "$tmp/documents.tsv" || true)" = "${docs_expected:-x}" ] \
    || fail "в списке документов $(grep -c . "$tmp/documents.tsv" || true) строк, в манифесте ${docs_expected:-нет числа}"
log "расшифровка: в порядке (документов в копии: ${docs_expected:-?})"

# --- 3. Одноразовый PostgreSQL ---------------------------------------------
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

# --- 4. Таблицы, миграции, расширения ----------------------------------------
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

# --- 6–7. Файлы томов и документы -----------------------------------------------
# Архив распаковывается во временный том (как при настоящем восстановлении),
# затем внутри контейнера `sha256sum -c` сверяет каждый файл со списком.
check_documents() {  # check_documents <временный том с документами>
    local target="$1"
    # Живые документы ВОССТАНОВЛЕННОЙ базы — тем же запросом, что при копировании.
    echo "SELECT format('SELECT storage_key, checksum_sha256 FROM public.files WHERE deleted_at IS NULL AND storage_provider = %L ORDER BY storage_key', 'local') WHERE to_regclass('public.files') IS NOT NULL
\\gexec" | psql_q > "$tmp/db-documents.tsv"
    sort "$tmp/db-documents.tsv" > "$tmp/db-documents.sorted"
    sort "$tmp/documents.tsv" > "$tmp/copy-documents.sorted"
    if ! diff -q "$tmp/db-documents.sorted" "$tmp/copy-documents.sorted" >/dev/null; then
        # Ключи хранения случайные и персональных данных не содержат.
        local only_db only_copy
        only_db="$(comm -23 "$tmp/db-documents.sorted" "$tmp/copy-documents.sorted" | grep -c . || true)"
        only_copy="$(comm -13 "$tmp/db-documents.sorted" "$tmp/copy-documents.sorted" | grep -c . || true)"
        comm -3 "$tmp/db-documents.sorted" "$tmp/copy-documents.sorted" | cut -f1 | sed 's/^[[:space:]]*/    /' | head -n 5 >&2
        fail "расхождение базы с архивом: записей files только в восстановленной базе — $only_db, только в списке копии — $only_copy"
    fi
    local total
    total="$(grep -c . "$tmp/db-documents.tsv" || true)"
    if [ "$total" -eq 0 ]; then
        log "документы: в восстановленной базе нет живых записей files"
        return 0
    fi
    # Каждый документ восстановленной базы — в восстановленном томе, с тем же хэшем.
    awk -F'\t' '{print $2 "  ./" $1}' "$tmp/db-documents.tsv" > "$tmp/db-documents.check"
    helper -i -v "$target:/dst:ro" "$BACKUP_HELPER_IMAGE" sh -c 'cd /dst && sha256sum -c - 2>/dev/null' \
        < "$tmp/db-documents.check" > "$tmp/db-documents.result" || true
    local missing changed
    missing="$(grep -c ': FAILED open or read$' "$tmp/db-documents.result" || true)"
    changed="$(grep -c ': FAILED$' "$tmp/db-documents.result" || true)"
    local ok
    ok="$(grep -c ': OK$' "$tmp/db-documents.result" || true)"
    if [ "$missing" -gt 0 ] || [ "$changed" -gt 0 ] || [ "$ok" -ne "$total" ]; then
        grep -v ': OK$' "$tmp/db-documents.result" | sed 's/^/    /' | head -n 5 >&2
        [ "$missing" -gt 0 ] && fail "документы: нет файла в восстановленном томе — $missing из $total"
        [ "$changed" -gt 0 ] && fail "документы: подменено содержимое (SHA-256 не совпадает с files.checksum_sha256) — $changed из $total"
        [ "$missing" -eq 0 ] && [ "$changed" -eq 0 ] && fail "документы: подтверждено $ok из $total"
        return 0
    fi
    log "документы: все $total записей files восстановленной базы найдены в восстановленном томе, SHA-256 совпадают"
}

volumes_seen=0
documents_checked=0
while IFS=$'\t' read -r kind key vol expected; do
    [ "$kind" = "volume" ] || continue
    volumes_seen=$((volumes_seen + 1))
    arch="$(find_part "volume-$key\\.tar\\.gz")"
    sums="$(find_part "volume-$key\\.sha256")"
    [ -n "$arch" ] || { fail "нет архива тома $key"; continue; }
    [ -n "$sums" ] || { fail "нет списка контрольных сумм тома $key"; continue; }
    decrypt_file "$set_dir/$arch" "$tmp/vol.tar.gz"
    decrypt_file "$set_dir/$sums" "$tmp/vol.sha256"
    target="${container}_vol_$key"
    docker volume create --label humotech.purpose=restore-test "$target" >/dev/null
    restore_volumes+=("$target")
    if ! helper -i -v "$target:/dst" "$BACKUP_HELPER_IMAGE" tar -xzf - -C /dst < "$tmp/vol.tar.gz"; then
        fail "том $key: архив не распаковался"; rm -f "$tmp/vol.tar.gz" "$tmp/vol.sha256"; continue
    fi
    got="$(helper -v "$target:/dst:ro" "$BACKUP_HELPER_IMAGE" sh -c 'find /dst -type f | wc -l' | tr -dc '0-9')"
    if [ ! -s "$tmp/vol.sha256" ]; then
        # Пустой том: сверять нечего (sha256sum -c на пустом списке —
        # ошибка), важно лишь, что и восстановилось ноль файлов.
        sums_ok=1
    elif helper -i -v "$target:/dst:ro" "$BACKUP_HELPER_IMAGE" \
            sh -c 'cd /dst && sha256sum --quiet -c -' < "$tmp/vol.sha256" >/dev/null 2>&1; then
        sums_ok=1
    else
        sums_ok=0
    fi
    rm -f "$tmp/vol.tar.gz" "$tmp/vol.sha256"
    if [ "$sums_ok" -ne 1 ]; then
        fail "том $key: восстановленные файлы не совпадают с контрольными суммами"
    elif [ "$got" != "$expected" ]; then
        fail "том $key: восстановлено файлов $got, ожидалось $expected"
    elif [ "$got" = 0 ]; then
        log "том $key ($vol): пуст, как и при копировании"
    else
        log "том $key ($vol): восстановлено файлов $got, SHA-256 всех совпали"
    fi
    if [ "$key" = "$media_key" ]; then
        check_documents "$target"
        documents_checked=1
    fi
    docker volume rm -f "$target" >/dev/null 2>&1 || true
done < "$set_dir/manifest.tsv"
[ "$volumes_seen" -gt 0 ] || fail "в манифесте нет ни одного тома с файлами"
[ "$documents_checked" = 1 ] || fail "том документов $media_key не найден среди томов копии — документы не проверены"

if [ "$failures" -gt 0 ]; then
    log "ПРОВЕРКА НЕ ПРОЙДЕНА: расхождений $failures"
    exit 1
fi
log "ПРОВЕРКА ПРОЙДЕНА: копия $(basename "$set_dir") восстанавливается полностью (база, файлы и документы)"
