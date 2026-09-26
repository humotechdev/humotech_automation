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
# отсутствии). Случаи с age и rclone выполняются, только если эти
# программы есть; иначе они явно помечаются SKIP, а итог — «НЕ ВСЁ
# ПРОВЕРЕНО». Полный набор — в Linux-стенде tests/lab (см. tests/lab/README.md).
# Внешнее хранилище rclone здесь — локальный каталог (type=local), в сеть
# ничего не уходит. Ключи age временные и после прогона удаляются.
set -Eeuo pipefail
export MSYS_NO_PATHCONV=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BK="$(cd "$HERE/.." && pwd)"
IMAGE="${BACKUP_TEST_IMAGE:-pgvector/pgvector:pg18}"

rand="$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
P="hbt_$rand"                         # имя «проекта compose»
PG="${P}-postgres-1"
MEDIA="${P}_private_media" EXPORTS="${P}_private_exports"
NOTAR="hbt-notar:$rand" BADTAR="hbt-badtar:$rand" FLAKY="hbt-flaky:$rand"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/hbt.XXXXXX")"
passed=0 failed=0 skipped=0

cleanup() {
    docker rm -f -v "$PG" >/dev/null 2>&1 || true
    docker volume rm -f "$MEDIA" "$EXPORTS" >/dev/null 2>&1 || true
    docker rmi -f "$NOTAR" "$BADTAR" "$FLAKY" >/dev/null 2>&1 || true
    # тома, которые мог оставить прерванный restore-test
    docker volume ls -q --filter label=humotech.purpose=restore-test | grep "restore_test" | xargs -r docker volume rm -f >/dev/null 2>&1 || true
    rm -rf "$tmp"
}
trap cleanup EXIT

say() { printf '\n=== %s\n' "$*"; }
ok() { passed=$((passed + 1)); printf '  PASS  %s\n' "$*"; }
bad() { failed=$((failed + 1)); printf '  FAIL  %s\n' "$*"; }
skip() { skipped=$((skipped + 1)); printf '  SKIP  %s\n' "$*"; }
tail_out() { printf '%s\n' "$1" | tail -n "${2:-6}" | sed 's/^/        /'; }

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
sql() { docker exec -i "$PG" psql -X -q -At -v ON_ERROR_STOP=1 -U humotech -d humotech_django; }
sql >/dev/null <<'SQL'
CREATE EXTENSION vector;
CREATE EXTENSION btree_gist;
CREATE TABLE django_migrations (id serial PRIMARY KEY, app text, name text, applied timestamptz DEFAULT now());
INSERT INTO django_migrations (app, name) SELECT 'employees', format('%04s_fake', g) FROM generate_series(1, 9) g;
INSERT INTO django_migrations (app, name) SELECT 'files', format('%04s_fake', g) FROM generate_series(1, 2) g;
CREATE TABLE employees (id serial PRIMARY KEY, full_name text, embedding vector(3));
INSERT INTO employees (full_name, embedding) SELECT 'Вымышленный Сотрудник ' || g, '[1,2,3]' FROM generate_series(1, 250) g;
-- Без уникального ключа намеренно: иначе не проверить повторяющийся ключ.
CREATE TABLE files (id serial PRIMARY KEY, storage_provider text NOT NULL DEFAULT 'local',
                    storage_key text NOT NULL, checksum_sha256 text NOT NULL, deleted_at timestamptz);
SQL
for v in "$MEDIA:private_media" "$EXPORTS:private_exports"; do
    docker volume create --label com.docker.compose.project="$P" \
        --label com.docker.compose.volume="${v#*:}" "${v%%:*}" >/dev/null
done
# Том документов: 3 живых документа (ключи как у store(): случайные),
# 1 файл удалённой записи, 1 посторонний файл с кириллицей и пробелом.
DOCS="absences/Kx9_aB3-cD.pdf absences/Qw2-Er5_Ty.pdf employees/Zx8_Cv7-Bn.png"
docker run --rm --network none -v "$MEDIA:/m" "$IMAGE" sh -c '
    mkdir -p /m/absences /m/employees /m/misc
    printf "%%PDF-1.4 fake a\n" > /m/absences/Kx9_aB3-cD.pdf
    printf "%%PDF-1.4 fake b\n" > /m/absences/Qw2-Er5_Ty.pdf
    head -c 4096 /dev/urandom > /m/employees/Zx8_Cv7-Bn.png
    printf "%%PDF-1.4 old\n" > /m/absences/old.pdf
    printf "fake\n" > "/m/misc/справка №2 копия.txt"'
# Записи files — с настоящими хэшами файлов.
docker run --rm --network none -v "$MEDIA:/m:ro" "$IMAGE" sh -c "cd /m && sha256sum $DOCS absences/old.pdf" \
    | awk '{ del = ($2 == "absences/old.pdf") ? "now()" : "NULL"
             printf "INSERT INTO files (storage_key, checksum_sha256, deleted_at) VALUES (%c%s%c, %c%s%c, %s);\n", 39, $2, 39, 39, $1, 39, del }' \
    | sql >/dev/null

build_helper() {  # build_helper <тег> <тело скрипта /usr/local/bin/tar>
    local ctx="$tmp/ctx-${1%%:*}"
    mkdir -p "$ctx"
    printf '#!/bin/sh\n%s\n' "$2" > "$ctx/tar"
    printf 'FROM %s\nCOPY tar /usr/local/bin/tar\nRUN chmod 755 /usr/local/bin/tar\n' "$IMAGE" > "$ctx/Dockerfile"
    # Git Bash на Windows: docker.exe не понимает путь /tmp/... — нужен Windows-путь.
    docker build -q -t "$1" "$(cygpath -w "$ctx" 2>/dev/null || echo "$ctx")" >/dev/null
}
# tar, который всегда падает; tar, который «успешно» выдаёт мусор; tar,
# поведение которого задают файлы-метки в самом томе: .exit2 — фатальная
# ошибка, .hide — список путей, которые «пропали во время копирования»,
# .exit1 — архив цел, но код 1 («файл изменился во время чтения»).
build_helper "$NOTAR" 'echo "simulated tar failure" >&2; exit 2'
build_helper "$BADTAR" 'case "$*" in *-czf*) echo not-a-gzip-archive; exit 0;; esac; exec /usr/bin/tar "$@"'
build_helper "$FLAKY" 'case "$*" in *-czf*)
  if [ -e /src/.exit2 ]; then echo "tar: simulated fatal error" >&2; exit 2; fi
  ex=""; [ -e /src/.hide ] && ex="--exclude-from=/src/.hide"
  /usr/bin/tar $ex "$@"; rc=$?
  if [ -e /src/.exit1 ]; then echo "tar: ./x: file changed as we read it" >&2; exit 1; fi
  exit $rc;;
esac
exec /usr/bin/tar "$@"'

vol_sh() { docker run --rm --network none -v "$1:/v" "$IMAGE" sh -c "$2"; }

# --- запуск ---------------------------------------------------------------------
# run_backup <имя случая> [NAME=VALUE ...] — пишет конфиг, запускает backup.sh.
# Код выхода — в $rc, журнал — в $out, stdout — в $stdout, каталог копий — $bdir.
run_backup() {
    local name="$1"; shift
    bdir="$tmp/b-$name"
    cfg="$tmp/$name.env"
    {
        echo "BACKUP_COMPOSE_PROJECT=$P"
        echo "BACKUP_DIR=$bdir"
        echo "BACKUP_ENCRYPTION=none"
        echo "BACKUP_REMOTE_ENABLED=false"
        echo "BACKUP_ALLOW_EMPTY_VOLUMES=private_exports"
        echo "BACKUP_TRANSIENT_VOLUMES=private_exports"
        echo "BACKUP_MIN_FREE_MB=1"
        echo "BACKUP_HELPER_IMAGE=$IMAGE"
        echo "BACKUP_RESTORE_IMAGE=$IMAGE"
        echo "BACKUP_VERIFY_APP_IMAGE="
        local kv; for kv in "$@"; do printf '%s="%s"\n' "${kv%%=*}" "${kv#*=}"; done
    } > "$cfg"
    set +e
    stdout="$(BACKUP_CONFIG="$cfg" bash "$BK/backup.sh" 2>"$tmp/stderr")"; rc=$?
    set -e
    out="$(cat "$tmp/stderr")"$'\n'"$stdout"
    all_logs+="$out"
}
run_restore() {  # run_restore <конфиг> <каталог копии>
    set +e
    rout="$(BACKUP_CONFIG="$1" bash "$BK/restore-test.sh" "$2" 2>&1)"; rrc=$?
    set -e
    all_logs+="$rout"
}
all_logs=""
final_sets() { ls -1 "$bdir" 2>/dev/null | grep -cE '^20[0-9]{6}T[0-9]{6}Z$' || true; }
partials() { ls -1A "$bdir" 2>/dev/null | grep -c '^\.partial-' || true; }
last_set() { echo "$bdir/$(ls -1 "$bdir" | grep -E '^20[0-9]{6}T' | sort | tail -n 1)"; }

# expect_refused <случай> <шаблон сообщения> [<шаблон, которого быть НЕ должно>]
expect_refused() {
    local what="$1" pattern="$2" not="${3:-}"
    if [ "$rc" -eq 1 ] && grep -q -- "$pattern" <<<"$out" && [ "$(final_sets)" = 0 ] && [ "$(partials)" = 0 ] \
        && { [ -z "$not" ] || ! grep -q -- "$not" <<<"$out"; }; then
        ok "$what: отказ ($pattern), готового каталога нет"
    else
        bad "$what: rc=$rc, готовых каталогов $(final_sets), .partial $(partials)"
        tail_out "$out"
    fi
}
expect_restore_fail() {  # expect_restore_fail <случай> <шаблон>
    if [ "$rrc" -ne 0 ] && grep -q -- "$2" <<<"$rout" && grep -q "ПРОВЕРКА НЕ ПРОЙДЕНА\|ОШИБКА" <<<"$rout"; then
        ok "$1: восстановление не пройдено ($2)"
    else
        bad "$1: restore rc=$rrc"; tail_out "$rout" 8
    fi
}
# resum <каталог копии> — пересчитать SHA256SUMS (имитация подмены, которую
# суммы без подписи не ловят: проверяться должен уже смысл содержимого).
resum() { ( cd "$1" && for f in $(ls -1 | grep -v '^SHA256SUMS$'); do printf '%s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$f"; done > SHA256SUMS ); }
# rearchive <каталог копии> <команды над распакованным томом в /x> — пересобрать
# архив тома документов и его список сумм (для незашифрованной копии).
rearchive() {
    docker run --rm -i --network none "$IMAGE" sh -c "set -e; mkdir /x; tar -xzf - -C /x; cd /x; $2; tar -C /x -czf - ." \
        < "$1/volume-private_media.tar.gz" > "$tmp/re.tar.gz"
    mv "$tmp/re.tar.gz" "$1/volume-private_media.tar.gz"
    docker run --rm -i --network none "$IMAGE" sh -c 'set -e; mkdir /x; tar -xzf - -C /x; cd /x; find . -type f -exec sha256sum {} +' \
        < "$1/volume-private_media.tar.gz" > "$1/volume-private_media.sha256"
}

# === I. Копия и восстановление ===================================================
say "1. всё на месте: копия и полное восстановление (база, файлы, документы)"
run_backup ok
okset="$(last_set)"; okcfg="$cfg"
m="$okset/manifest.tsv"
if [ "$rc" -eq 0 ] && [ "$(final_sets)" = 1 ] && [ "$stdout" = "$okset" ] \
    && grep -q $'^volume\tprivate_media\t'"$MEDIA"$'\t5$' "$m" \
    && grep -q $'^volume\tprivate_exports\t'"$EXPORTS"$'\t0$' "$m" \
    && grep -q $'^meta\tdocuments\t3$' "$m" && grep -q $'^meta\tstatus\tcomplete$' "$m" \
    && grep -q "документы базы: все 3 найдены" <<<"$out"; then
    ok "копия создана; тома по меткам; 3 документа сверены с архивом по SHA-256"
else
    bad "копия: rc=$rc"; tail_out "$out" 8
fi
if ! grep -q 'absences/\|employees/' "$m" && [ -s "$okset/documents.tsv" ]; then
    ok "ключи документов — только в documents.tsv, в manifest.tsv их нет"
else
    bad "ключи документов попали в manifest.tsv"
fi
run_restore "$okcfg" "$okset"
if [ "$rrc" -eq 0 ] && grep -q "ПРОВЕРКА ПРОЙДЕНА" <<<"$rout" \
    && grep -q "том private_media ($MEDIA): восстановлено файлов 5, SHA-256 всех совпали" <<<"$rout" \
    && grep -q "документы: все 3 записей files восстановленной базы найдены в восстановленном томе" <<<"$rout" \
    && grep -q "таблиц: 3, строк всего: 265" <<<"$rout"; then
    ok "восстановление: база (3 таблицы, 265 строк), 5 файлов побайтно, 3 документа против восстановленной базы"
else
    bad "восстановление: rc=$rrc"; tail_out "$rout" 10
fi

say "2. восстановление: подменён документ (суммы тома и копии пересчитаны)"
cp -r "$okset" "$tmp/r-changed"
rearchive "$tmp/r-changed" 'echo tampered >> absences/Qw2-Er5_Ty.pdf'
resum "$tmp/r-changed"
run_restore "$okcfg" "$tmp/r-changed"
expect_restore_fail "подменённый документ" "подменено содержимое"

say "3. восстановление: документ отсутствует в архиве"
cp -r "$okset" "$tmp/r-missing"
rearchive "$tmp/r-missing" 'rm employees/Zx8_Cv7-Bn.png'
sed -i "s/^\(volume\tprivate_media\t[^\t]*\t\)5$/\14/" "$tmp/r-missing/manifest.tsv"
resum "$tmp/r-missing"
run_restore "$okcfg" "$tmp/r-missing"
expect_restore_fail "отсутствующий документ" "нет файла в восстановленном томе — 1 из 3"

say "4. восстановление: список документов копии расходится с восстановленной базой"
cp -r "$okset" "$tmp/r-diverge"
sed -i '/Kx9_aB3-cD/d' "$tmp/r-diverge/documents.tsv"
sed -i "s/^meta\tdocuments\t3$/meta\tdocuments\t2/" "$tmp/r-diverge/manifest.tsv"
resum "$tmp/r-diverge"
run_restore "$okcfg" "$tmp/r-diverge"
expect_restore_fail "база против архива" "расхождение базы с архивом: записей files только в восстановленной базе — 1"

# === II. Отказы при копировании ===================================================
say "5. том не найден / пуст"
run_backup missing "BACKUP_COMPOSE_VOLUMES=private_media private_exports private_scans"
expect_refused "том не найден" "том private_scans проекта $P НЕ НАЙДЕН" "ПУСТ"
run_backup empty "BACKUP_ALLOW_EMPTY_VOLUMES="
expect_refused "том пуст" "том private_exports ($EXPORTS) существует, но ПУСТ" "НЕ НАЙДЕН"

say "6. документ пропал, а число файлов прежнее"
vol_sh "$MEDIA" 'mv /v/absences/Qw2-Er5_Ty.pdf /v/absences/Qw2-Er5_Ty.bak; printf x > /v/misc/orphan.pdf'
run_backup doc_missing
expect_refused "нет одного документа" "из 3 нет в копии 1, с другим содержимым 0"
vol_sh "$MEDIA" 'mv /v/absences/Qw2-Er5_Ty.bak /v/absences/Qw2-Er5_Ty.pdf; rm /v/misc/orphan.pdf'

say "7. хэш документа в базе не совпадает с файлом"
sql <<<"UPDATE files SET checksum_sha256 = repeat('0', 64) WHERE storage_key = 'absences/Kx9_aB3-cD.pdf'" >/dev/null
run_backup doc_changed
expect_refused "другое содержимое" "из 3 нет в копии 0, с другим содержимым 1"
# вернуть настоящий хэш
docker run --rm --network none -v "$MEDIA:/m:ro" "$IMAGE" sha256sum /m/absences/Kx9_aB3-cD.pdf \
    | awk '{printf "UPDATE files SET checksum_sha256 = %c%s%c WHERE storage_key = %cabsences/Kx9_aB3-cD.pdf%c;\n", 39, $1, 39, 39, 39}' | sql >/dev/null

say "8. повторяющийся и неоднозначный ключи"
sql <<<"INSERT INTO files (storage_key, checksum_sha256) SELECT storage_key, checksum_sha256 FROM files WHERE storage_key = 'absences/Qw2-Er5_Ty.pdf'" >/dev/null
run_backup dup_key
expect_refused "повторяющийся ключ" "ключ повторяется: absences/Qw2-Er5_Ty.pdf"
sql <<<"DELETE FROM files WHERE id = (SELECT max(id) FROM files)" >/dev/null
sql <<<"INSERT INTO files (storage_key, checksum_sha256) VALUES ('absences/../Kx9_aB3-cD.pdf', repeat('a', 64)), ('absences//x.pdf', repeat('b', 64))" >/dev/null
run_backup odd_key
expect_refused "неоднозначный ключ" "неоднозначный ключ: absences/../Kx9_aB3-cD.pdf"
grep -q "недопустимый ключ: absences//x.pdf" <<<"$out" && ok "ключ с пустой частью пути отклонён" || bad "ключ absences//x.pdf не отклонён"
sql <<<"DELETE FROM files WHERE storage_key IN ('absences/../Kx9_aB3-cD.pdf', 'absences//x.pdf')" >/dev/null

say "9. документы: том пуст при живых файлах в базе"
# сохранить том, чтобы вернуть его после случая
docker run --rm --network none -v "$MEDIA:/v" "$IMAGE" sh -c 'tar -C /v -cf - .' > "$tmp/media-backup.tar"
vol_sh "$MEDIA" 'find /v -mindepth 1 -delete'
run_backup media_wiped "BACKUP_ALLOW_EMPTY_VOLUMES=private_exports private_media"
expect_refused "том документов опустошён (пустой разрешён)" "из 3 нет в копии 3"
run_backup media_wiped2
expect_refused "том документов опустошён" "том документов private_media ($MEDIA) существует, но ПУСТ, а база ссылается на 3 живых файлов"
docker run --rm -i --network none -v "$MEDIA:/v" "$IMAGE" tar -C /v -xf - < "$tmp/media-backup.tar"

say "10. tar: ошибка, мусор, пропажа файлов во время копирования"
run_backup tar_fails "BACKUP_HELPER_IMAGE=$NOTAR"
expect_refused "tar с ошибкой" "архивация завершилась с ошибкой (tar, код 2)"
run_backup tar_garbage "BACKUP_HELPER_IMAGE=$BADTAR"
expect_refused "мусор вместо архива" "архив не распаковывается"
vol_sh "$MEDIA" 'printf "./misc\n" > /v/.hide'
run_backup media_shrunk "BACKUP_HELPER_IMAGE=$FLAKY"
expect_refused "постоянный том: файл пропал во время копирования" "файлы пропали во время копирования, копия не создаётся"
vol_sh "$MEDIA" 'rm /v/.hide; touch /v/.exit1'
run_backup media_tar1 "BACKUP_HELPER_IMAGE=$FLAKY"
expect_refused "постоянный том: tar код 1" "архивация завершилась с ошибкой (tar, код 1)"
vol_sh "$MEDIA" 'rm /v/.exit1'
vol_sh "$EXPORTS" 'printf x > /v/report-1.xlsx; printf y > /v/gone.xlsx; printf "./gone.xlsx\n" > /v/.hide; touch /v/.exit1'
run_backup exports_shrunk "BACKUP_HELPER_IMAGE=$FLAKY"
if [ "$rc" -eq 0 ] && [ "$(final_sets)" = 1 ] \
    && grep -q "ВНИМАНИЕ: том private_exports ($EXPORTS, временный): файлы менялись во время копирования (tar, код 1)" <<<"$out" \
    && grep -q "ВНИМАНИЕ: том private_exports ($EXPORTS, временный): перед архивацией файлов 4, в архиве 3" <<<"$out" \
    && grep -q $'^warning\tvolume_shrunk\tprivate_exports\t4\t3$' "$(last_set)/manifest.tsv" \
    && grep -q $'^warning\ttar_changed\tprivate_exports$' "$(last_set)/manifest.tsv"; then
    ok "временный том: пропажа файла и tar код 1 — предупреждение в журнале и манифесте, копия создана"
else
    bad "временный том: rc=$rc"; tail_out "$out" 8
fi
vol_sh "$EXPORTS" 'rm /v/.exit1; touch /v/.exit2'
run_backup exports_tar2 "BACKUP_HELPER_IMAGE=$FLAKY"
expect_refused "временный том: tar код 2" "том private_exports ($EXPORTS): архивация завершилась с ошибкой (tar, код 2)"
vol_sh "$EXPORTS" 'find /v -mindepth 1 -delete'

say "11. мало места на диске"
run_backup no_space "BACKUP_MIN_FREE_MB=999999999"
expect_refused "мало места" "нужно не меньше 999999999 (BACKUP_MIN_FREE_MB) — копия не начата"

say "12. режим явных имён"
run_backup explicit "BACKUP_COMPOSE_PROJECT=" "BACKUP_PG_CONTAINER=$PG" "BACKUP_VOLUMES=$MEDIA $EXPORTS" \
    "BACKUP_ALLOW_EMPTY_VOLUMES=$EXPORTS" "BACKUP_TRANSIENT_VOLUMES=$EXPORTS"
[ "$rc" -eq 0 ] && [ "$(final_sets)" = 1 ] && ok "явные имена: копия создана" \
    || { bad "явные имена: rc=$rc"; tail_out "$out"; }
run_backup explicit_missing "BACKUP_COMPOSE_PROJECT=" "BACKUP_PG_CONTAINER=$PG" "BACKUP_VOLUMES=$MEDIA ${P}_nope"
expect_refused "явное имя тома не найдено" "том ${P}_nope НЕ НАЙДЕН"

# === III. Шифрование age ===========================================================
say "13. age: зашифрованный цикл, неверный ключ, повреждённый архив"
if command -v age >/dev/null 2>&1 && command -v age-keygen >/dev/null 2>&1; then
    # Ключи временные: закрытые — только в файлах $tmp (права 600), на экран
    # не выводятся; открытый получается из закрытого.
    ( umask 077; age-keygen -o "$tmp/owner.key" 2>/dev/null; age-keygen -o "$tmp/stranger.key" 2>/dev/null )
    recipient="$(age-keygen -y "$tmp/owner.key")"
    run_backup age "BACKUP_ENCRYPTION=age" "BACKUP_AGE_RECIPIENT=$recipient"
    ageset="$(last_set)"
    if [ "$rc" -eq 0 ] && [ -f "$ageset/db.dump.age" ] && [ -f "$ageset/documents.tsv.age" ] \
        && [ -f "$ageset/volume-private_media.tar.gz.age" ] \
        && [ -z "$(ls -1 "$ageset" | grep -E '^(db\.dump|documents\.tsv|volume-.*\.(tar\.gz|sha256))$')" ]; then
        ok "age: копия зашифрована, открытых дампа, архивов и списка документов в каталоге нет"
    else
        bad "age: rc=$rc"; tail_out "$out"
    fi
    printf 'BACKUP_AGE_IDENTITY_FILE=%s\nBACKUP_HELPER_IMAGE=%s\nBACKUP_RESTORE_IMAGE=%s\n' "$tmp/owner.key" "$IMAGE" "$IMAGE" > "$tmp/owner.env"
    run_restore "$tmp/owner.env" "$ageset"
    if [ "$rrc" -eq 0 ] && grep -q "ПРОВЕРКА ПРОЙДЕНА" <<<"$rout" && grep -q "документы: все 3" <<<"$rout"; then
        ok "age: расшифровка и полное восстановление (база, файлы, документы)"
    else
        bad "age: восстановление rc=$rrc"; tail_out "$rout" 8
    fi
    printf 'BACKUP_AGE_IDENTITY_FILE=%s\nBACKUP_HELPER_IMAGE=%s\nBACKUP_RESTORE_IMAGE=%s\n' "$tmp/stranger.key" "$IMAGE" "$IMAGE" > "$tmp/stranger.env"
    run_restore "$tmp/stranger.env" "$ageset"
    expect_restore_fail "age: неверный ключ" "не удалось расшифровать db.dump.age: неверный ключ или файл повреждён"
    cp -r "$ageset" "$tmp/age-broken"
    f="$tmp/age-broken/volume-private_media.tar.gz.age"
    b="$(dd if="$f" bs=1 skip=300 count=1 2>/dev/null | od -An -tu1 | tr -d ' ')"
    printf "$(printf '\\%03o' $(( (b + 1) % 256 )))" | dd of="$f" bs=1 seek=300 conv=notrunc 2>/dev/null
    run_restore "$tmp/owner.env" "$tmp/age-broken"
    expect_restore_fail "age: повреждённый архив, суммы не пересчитаны" "контрольная сумма не сходится"
    resum "$tmp/age-broken"
    run_restore "$tmp/owner.env" "$tmp/age-broken"
    expect_restore_fail "age: повреждённый архив, суммы пересчитаны" "не удалось расшифровать volume-private_media.tar.gz.age"
    if grep -q "AGE-SECRET-KEY" <<<"$all_logs"; then bad "закрытый ключ попал в журнал"; else ok "закрытый ключ во всех журналах прогона не встречается"; fi
else
    skip "age не установлен — зашифрованный цикл не проверен"
fi

# === IV. Внешняя копия rclone ========================================================
say "14. rclone на локальном хранилище: отправка, сбой, повтор, ротация, восстановление"
if command -v rclone >/dev/null 2>&1 && command -v age >/dev/null 2>&1; then
    export RCLONE_CONFIG_OFFSITE_TYPE=local     # тот же remote для проверок из теста
    mkdir -p "$tmp/offsite"; printf 'x' > "$tmp/blocker"   # файл вместо каталога — отправка невозможна
    remote_ok="offsite:$tmp/offsite/prod" remote_bad="offsite:$tmp/blocker/prod"
    rbase=(BACKUP_ENCRYPTION=age "BACKUP_AGE_RECIPIENT=$recipient" BACKUP_REMOTE_ENABLED=true
           RCLONE_CONFIG_OFFSITE_TYPE=local BACKUP_REMOTE_KEEP=0 BACKUP_UNSENT_WARN=2)
    sent() { ls -1 "$bdir/.remote-sent" 2>/dev/null | grep -c . || true; }

    run_backup remote "${rbase[@]}" "BACKUP_RCLONE_REMOTE=$remote_ok"
    first="$(basename "$(last_set)")"
    if [ "$rc" -eq 0 ] && [ "$(sent)" = 1 ] && grep -q "отправлено и сверено: $remote_ok/$first" <<<"$out" \
        && rclone check --one-way "$bdir/$first" "$remote_ok/$first" >/dev/null 2>&1; then
        ok "первая отправка: копия ушла, rclone check сошёлся, отметка поставлена"
    else
        bad "первая отправка: rc=$rc"; tail_out "$out"
    fi
    sleep 1
    run_backup remote "${rbase[@]}" "BACKUP_RCLONE_REMOTE=$remote_ok"
    [ "$rc" -eq 0 ] && [ "$(sent)" = 2 ] && [ "$(grep -c 'отправлено и сверено' <<<"$out")" = 1 ] \
        && ok "повторный запуск: отправлена только новая копия" || { bad "повторный запуск: rc=$rc"; tail_out "$out"; }

    sleep 1
    run_backup remote "${rbase[@]}" "BACKUP_RCLONE_REMOTE=$remote_bad"
    third="$(basename "$(last_set)")"
    if [ "$rc" -eq 75 ] && [ -z "$stdout" ] && [ -d "$bdir/$third" ] && [ "$(sent)" = 2 ] \
        && grep -q "ОШИБКА: во внешнее хранилище НЕ отправлены копии: $third. Локальная копия $third создана и сохранена" <<<"$out" \
        && ! grep -q "НЕ создана" <<<"$out"; then
        ok "сбой отправки: код 75, «НЕ отправлены», локальная копия цела, ложного «НЕ создана» нет"
    else
        bad "сбой отправки: rc=$rc"; tail_out "$out"
    fi
    sleep 1
    run_backup remote "${rbase[@]}" "BACKUP_RCLONE_REMOTE=$remote_bad" BACKUP_KEEP_DAILY=1 BACKUP_KEEP_MONTHLY=0
    fourth="$(basename "$(last_set)")"
    if [ "$rc" -eq 75 ] && [ -d "$bdir/$third" ] && [ -d "$bdir/$fourth" ] && [ ! -d "$bdir/$first" ] \
        && grep -q "ротация: копия $third НЕ удалена — она ещё не отправлена" <<<"$out" \
        && grep -q "ВНИМАНИЕ: 2 копий ждут отправки" <<<"$out"; then
        ok "ротация: отправленные удалены, неотправленные сохранены; предупреждение о накоплении"
    else
        bad "ротация при сбое отправки: rc=$rc"; tail_out "$out" 8
    fi
    sleep 1
    run_backup remote "${rbase[@]}" "BACKUP_RCLONE_REMOTE=$remote_ok"
    if [ "$rc" -eq 0 ] && grep -q "отправлено и сверено: $remote_ok/$third" <<<"$out" \
        && grep -q "отправлено и сверено: $remote_ok/$fourth" <<<"$out" && [ -e "$bdir/.remote-sent/$third" ]; then
        ok "хранилище вернулось: накопившиеся копии отправлены и сверены"
    else
        bad "повторная доставка: rc=$rc"; tail_out "$out" 8
    fi
    mkdir -p "$tmp/downloaded"
    RCLONE_CONFIG_OFFSITE_TYPE=local rclone copy "$remote_ok/$third" "$tmp/downloaded/$third" >/dev/null 2>&1
    run_restore "$tmp/owner.env" "$tmp/downloaded/$third"
    if [ "$rrc" -eq 0 ] && grep -q "ПРОВЕРКА ПРОЙДЕНА" <<<"$rout" && grep -q "документы: все 3" <<<"$rout"; then
        ok "восстановление из копии, скачанной из хранилища: база, файлы и документы"
    else
        bad "восстановление из хранилища: rc=$rrc"; tail_out "$rout" 8
    fi
    run_backup remote_plain BACKUP_ENCRYPTION=none BACKUP_REMOTE_ENABLED=true RCLONE_CONFIG_OFFSITE_TYPE=local \
        "BACKUP_RCLONE_REMOTE=$remote_ok"
    expect_refused "отправка без шифрования" "отправка наружу без шифрования запрещена"
elif ! command -v rclone >/dev/null 2>&1; then
    skip "rclone не установлен — внешняя копия не проверена"
else
    skip "rclone есть, но нет age — внешняя копия требует шифрования, не проверена"
fi

# === V. Прочее =======================================================================
say "15. новый сервер: том документов пуст, база на файлы не ссылается"
sql <<<"UPDATE files SET deleted_at = now()" >/dev/null
run_backup fresh "BACKUP_COMPOSE_VOLUMES=private_exports" "BACKUP_MEDIA_VOLUME=private_exports" \
    "BACKUP_ALLOW_EMPTY_VOLUMES=" "BACKUP_TRANSIENT_VOLUMES="
if [ "$rc" -eq 0 ] && [ "$(final_sets)" = 1 ] && grep -q "в базе нет ни одного живого файла — допустимо" <<<"$out"; then
    ok "пустой том документов при нуле живых файлов: копия создана"
else
    bad "новый сервер: rc=$rc"; tail_out "$out"
fi
run_backup media_transient "BACKUP_TRANSIENT_VOLUMES=private_exports private_media"
expect_refused "том документов объявлен временным" "не может быть временным"

say "16. контейнер базы остановлен"
docker stop -t 5 "$PG" >/dev/null
run_backup pg_stopped
expect_refused "база остановлена" "есть, но не запущен"

printf '\nИтог: %d PASS, %d FAIL, %d SKIP\n' "$passed" "$failed" "$skipped"
[ "$skipped" -eq 0 ] || echo "НЕ ВСЁ ПРОВЕРЕНО: пропущенные случаи выше (SKIP)"
[ "$failed" -eq 0 ]
