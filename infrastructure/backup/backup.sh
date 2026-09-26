#!/usr/bin/env bash
# Резервная копия HUMOTECH: база PostgreSQL и тома с файлами.
#
#   infrastructure/backup/backup.sh            # по настройкам из backup.env
#   BACKUP_CONFIG=/etc/humotech/backup.env infrastructure/backup/backup.sh
#
# Что получается — каталог $BACKUP_DIR/<UTC-время>/:
#   db.dump[.age|.gpg]              pg_dump -Fc рабочей базы
#   volume-<том>.tar.gz[.age|.gpg]  содержимое тома (private_media, private_exports)
#   volume-<том>.sha256[.age|.gpg]  SHA-256 каждого файла ИЗ АРХИВА (архив
#                                   распакован и пересчитан — он читается)
#   manifest.tsv                    число строк в КАЖДОЙ таблице, миграции,
#                                   расширения, живые строки files, число
#                                   файлов в каждом томе, отметка complete
#   SHA256SUMS                      контрольные суммы всего, что выше
#
# Каталог с таким именем появляется ТОЛЬКО после всех проверок. До этого
# копия собирается в .partial-<время> и при любой ошибке удаляется, а
# скрипт завершается с ненулевым кодом. Ошибкой считается:
#   * контейнер базы или обязательный том не найден («НЕ НАЙДЕН»);
#   * том существует, но пуст, и он не указан в BACKUP_ALLOW_EMPTY_VOLUMES
#     («существует, но ПУСТ») — это разные сообщения намеренно. Том с
#     документами (BACKUP_MEDIA_VOLUME) может быть пустым, только если в
#     базе нет ни одной живой строки files (новый сервер);
#   * tar завершился с ошибкой или архив не распаковывается;
#   * в томе с документами файлов меньше, чем живых строк в таблице files.
#
# Почему числа строк точные. Дамп и подсчёт идут из одного снимка базы
# (pg_export_snapshot + pg_dump --snapshot): что посчитано, то и в дампе.
# Поэтому restore-test.sh сверяет таблицы на равенство, а не «примерно».
#
# Скрипт только ЧИТАЕТ базу и тома: pg_dump в транзакции READ ONLY, тома
# подключаются во вспомогательный контейнер с флагом :ro.
#
# Шифрование — открытым ключом (age или gpg). Отправка наружу (rclone) по
# умолчанию выключена и без шифрования не выполняется вовсе.
set -Eeuo pipefail
umask 077
LOG_TAG=backup
# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config
need docker
# Источник проверяется до того, как появится хоть один файл копии.
resolve_sources

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

# Два запуска одновременно (таймер + ручной) писали бы в один снимок тома
# и удаляли бы чужие каталоги ротацией. flock есть на любом Linux; на
# машине разработчика без него просто идём дальше.
if command -v flock >/dev/null 2>&1; then
    exec 9>"$BACKUP_DIR/.lock"
    flock -n 9 || die "резервное копирование уже идёт"
fi

work="$BACKUP_DIR/.partial-$stamp"
final="$BACKUP_DIR/$stamp"
remote_dump="/tmp/humotech-backup-$stamp.dump"
mkdir -p "$work"

cleanup() {
    local rc=$?
    docker exec "$PG_CONTAINER" rm -f "$remote_dump" >/dev/null 2>&1 || true
    if [ $rc -ne 0 ]; then
        rm -rf "$work"
        log "копия НЕ создана (код $rc), незавершённый каталог удалён"
    fi
}
trap cleanup EXIT

if [ "$BACKUP_REMOTE_ENABLED" = "true" ] && [ "$BACKUP_ENCRYPTION" = "none" ]; then
    die "отправка наружу без шифрования запрещена: задайте BACKUP_ENCRYPTION=age|gpg"
fi

# --- 1. База ---------------------------------------------------------------
log "база: $BACKUP_PG_DB из контейнера $PG_CONTAINER"
docker exec "$PG_CONTAINER" pg_isready -U "$BACKUP_PG_USER" -d "$BACKUP_PG_DB" >/dev/null \
    || die "PostgreSQL в $PG_CONTAINER не отвечает"

# Внутри контейнера подключение через unix-сокет: пароль не нужен и не
# передаётся ни в командной строке, ни в окружении.
# `\!` выполняется, пока транзакция psql открыта, — снимок жив всё время дампа.
docker exec -i "$PG_CONTAINER" \
    psql -X -q -At -F $'\t' -v ON_ERROR_STOP=1 -U "$BACKUP_PG_USER" -d "$BACKUP_PG_DB" \
    > "$work/manifest.tsv" <<SQL
BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;
SELECT pg_export_snapshot() AS snap \gset
\setenv HT_SNAP :snap
\! pg_dump -U "$BACKUP_PG_USER" -d "$BACKUP_PG_DB" -Fc -Z 6 --snapshot="\$HT_SNAP" -f "$remote_dump" || echo "pg_dump-failed"
SELECT 'meta', 'database', current_database();
SELECT 'meta', 'server_version', current_setting('server_version');
SELECT 'meta', 'created_utc', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
SELECT 'extension', extname, extversion FROM pg_extension ORDER BY extname;
SELECT 'migrations', app, count(*) FROM django_migrations GROUP BY app ORDER BY app;
SELECT format('SELECT %L, %L, count(*) FROM public.files WHERE deleted_at IS NULL', 'meta', 'files_live')
 WHERE to_regclass('public.files') IS NOT NULL
\gexec
SELECT format('SELECT %L, %L, count(*) FROM %I.%I', 'table', schemaname || '.' || tablename, schemaname, tablename)
  FROM pg_tables
 WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
 ORDER BY schemaname, tablename
\gexec
COMMIT;
SQL

grep -q '^pg_dump-failed' "$work/manifest.tsv" && die "pg_dump завершился с ошибкой"
docker exec "$PG_CONTAINER" test -s "$remote_dump" || die "pg_dump не создал файл"
docker exec "$PG_CONTAINER" cat "$remote_dump" > "$work/db.dump"
docker exec "$PG_CONTAINER" rm -f "$remote_dump"
# Дамп читается pg_restore? Проверка оглавления ловит обрезанный файл сразу,
# а не через месяц на проверке восстановления.
docker exec -i "$PG_CONTAINER" pg_restore -l < "$work/db.dump" > /dev/null \
    || die "дамп не читается pg_restore"
tables=$(grep -c '^table' "$work/manifest.tsv" || true)
log "база: дамп $(du -h "$work/db.dump" | cut -f1), таблиц в манифесте: $tables"

# --- 2. Тома с файлами ------------------------------------------------------
# Для каждого тома: сколько файлов в нём сейчас → архив → архив распакован
# во вспомогательном контейнере и пересчитан по SHA-256. Любой сбой — die.
helper() { docker run --rm --network none "$@"; }
# Живые строки files из того же снимка, что и дамп (пусто — таблицы нет).
live="$(awk -F'\t' '$1=="meta" && $2=="files_live" {print $3}' "$work/manifest.tsv")"
for i in "${!VOL_KEYS[@]}"; do
    key="${VOL_KEYS[$i]}" vol="${VOL_NAMES[$i]}"
    present="$(helper -v "$vol:/src:ro" "$BACKUP_HELPER_IMAGE" sh -c 'find /src -type f | wc -l')" \
        || die "том $key ($vol): не удалось прочитать содержимое"
    present="$(printf '%s' "$present" | tr -dc '0-9')"
    [ -n "$present" ] || die "том $key ($vol): не удалось посчитать файлы"
    if [ "$present" -eq 0 ]; then
        if in_list "$key" "$BACKUP_ALLOW_EMPTY_VOLUMES"; then
            log "том $key ($vol): существует, пуст (разрешено BACKUP_ALLOW_EMPTY_VOLUMES)"
        elif [ "$key" = "$BACKUP_MEDIA_VOLUME" ] && [ "$live" = "0" ]; then
            # Новый сервер: документов ещё не загружали, база ни на один
            # файл не ссылается. Пустой том здесь — правда, а не потеря.
            log "том $key ($vol): существует, пуст; в базе нет ни одного живого файла — допустимо"
        elif [ "$key" = "$BACKUP_MEDIA_VOLUME" ] && [ -n "$live" ]; then
            die "том документов $key ($vol) существует, но ПУСТ, а база ссылается на $live живых файлов — копия не создаётся"
        else
            die "том $key ($vol) существует, но ПУСТ — копия не создаётся. Если пустой том здесь нормален, добавьте $key в BACKUP_ALLOW_EMPTY_VOLUMES"
        fi
    fi
    helper -v "$vol:/src:ro" "$BACKUP_HELPER_IMAGE" tar -C /src -czf - . > "$work/volume-$key.tar.gz" \
        || die "том $key ($vol): архивация завершилась с ошибкой"
    helper -i "$BACKUP_HELPER_IMAGE" sh -c 'set -e; mkdir /x; tar -xzf - -C /x; cd /x; find . -type f -exec sha256sum {} +' \
        < "$work/volume-$key.tar.gz" > "$work/volume-$key.sha256" \
        || die "том $key ($vol): архив не распаковывается — копия повреждена"
    files="$(grep -c . "$work/volume-$key.sha256" || true)"
    if [ "$present" -gt 0 ] && [ "$files" -eq 0 ]; then
        die "том $key ($vol): в томе $present файлов, в архиве — ни одного"
    fi
    [ "$files" = "$present" ] \
        || log "том $key: файлов было $present, в архиве $files (изменились во время копирования)"
    printf 'volume\t%s\t%s\t%s\n' "$key" "$vol" "$files" >> "$work/manifest.tsv"
    log "том $key ($vol): файлов $files, архив $(du -h "$work/volume-$key.tar.gz" | cut -f1)"
done

# Документы, на которые ссылается база, должны быть в копии.
media="$(awk -F'\t' -v k="$BACKUP_MEDIA_VOLUME" '$1=="volume" && $2==k {print $4}' "$work/manifest.tsv")"
[ -n "$media" ] || die "том с документами $BACKUP_MEDIA_VOLUME не входит в список копируемых томов"
if [ -n "$live" ] && [ "$live" -gt "$media" ]; then
    die "в базе $live живых файлов, а в томе $BACKUP_MEDIA_VOLUME только $media — копия была бы неполной"
fi
printf 'meta\tstatus\tcomplete\n' >> "$work/manifest.tsv"

# --- 3. Шифрование и контрольные суммы -------------------------------------
suffix="$(enc_suffix)"
for f in "$work"/db.dump "$work"/volume-*.tar.gz "$work"/volume-*.sha256; do
    [ -e "$f" ] || continue
    encrypt_file "$f"
done
(
    cd "$work"
    for f in db.dump"$suffix" volume-*.tar.gz"$suffix" volume-*.sha256"$suffix" manifest.tsv; do
        [ -e "$f" ] && printf '%s  %s\n' "$(sha256_file "$f")" "$f"
    done
) > "$work/SHA256SUMS"
mv "$work" "$final"
log "копия готова: $final (шифрование: $BACKUP_ENCRYPTION)"

# --- 4. Ротация на сервере -------------------------------------------------
# Храним BACKUP_KEEP_DAILY последних копий и дополнительно первую копию
# каждого из BACKUP_KEEP_MONTHLY последних месяцев.
mapfile -t sets < <(ls -1 "$BACKUP_DIR" | grep -E '^20[0-9]{6}T[0-9]{6}Z$' | sort -r)
declare -A keep=() month_first=()
i=0
for s in "${sets[@]}"; do
    i=$((i + 1))
    [ "$i" -le "$BACKUP_KEEP_DAILY" ] && keep[$s]=1
    month_first[${s:0:6}]="$s"          # список идёт от новых к старым: остаётся самая ранняя
done
m=0
for mon in $(printf '%s\n' "${!month_first[@]}" | sort -r); do
    m=$((m + 1))
    [ "$m" -le "$BACKUP_KEEP_MONTHLY" ] && keep[${month_first[$mon]}]=1
done
for s in "${sets[@]}"; do
    if [ -z "${keep[$s]:-}" ]; then
        rm -rf "${BACKUP_DIR:?}/$s"
        log "ротация: удалена копия $s"
    fi
done
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '.partial-*' -mmin +1440 -exec rm -rf {} + 2>/dev/null || true

# --- 5. Копия вне сервера --------------------------------------------------
if [ "$BACKUP_REMOTE_ENABLED" = "true" ]; then
    need rclone
    [ -n "$BACKUP_RCLONE_REMOTE" ] || die "BACKUP_REMOTE_ENABLED=true, но не задан BACKUP_RCLONE_REMOTE"
    # Настройки хранилища rclone читает из RCLONE_CONFIG_<ИМЯ>_* (см.
    # backup.env.example): ключи доступа не лежат ни в одном файле скрипта.
    rclone copy --immutable --checksum "$final" "$BACKUP_RCLONE_REMOTE/$stamp"
    rclone check --one-way "$final" "$BACKUP_RCLONE_REMOTE/$stamp"
    log "отправлено: $BACKUP_RCLONE_REMOTE/$stamp"
    if [ -n "$BACKUP_REMOTE_KEEP" ] && [ "$BACKUP_REMOTE_KEEP" != "0" ]; then
        rclone delete --min-age "$BACKUP_REMOTE_KEEP" "$BACKUP_RCLONE_REMOTE"
        rclone rmdirs --leave-root "$BACKUP_RCLONE_REMOTE" || true
    fi
else
    log "копия вне сервера выключена (BACKUP_REMOTE_ENABLED=false)"
fi

echo "$final"
