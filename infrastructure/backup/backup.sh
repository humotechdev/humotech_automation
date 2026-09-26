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
#   documents.tsv[.age|.gpg]        живые строки files из того же снимка базы,
#                                   что и дамп: ключ хранения и SHA-256 каждого
#                                   документа (только в зашифрованной части)
#   manifest.tsv                    ТОЛЬКО числа: строки в каждой таблице,
#                                   миграции, расширения, число документов и
#                                   файлов в томах, предупреждения, complete
#   SHA256SUMS                      контрольные суммы всего, что выше
#
# Каталог с таким именем появляется ТОЛЬКО после всех проверок. До этого
# копия собирается в .partial-<время> и при любой ошибке удаляется, а
# скрипт завершается с кодом 1. Ошибкой считается:
#   * на разделе BACKUP_DIR свободно меньше BACKUP_MIN_FREE_MB;
#   * контейнер базы или обязательный том не найден («НЕ НАЙДЕН»);
#   * том существует, но пуст, и он не указан в BACKUP_ALLOW_EMPTY_VOLUMES
#     («существует, но ПУСТ») — это разные сообщения намеренно. Том с
#     документами (BACKUP_MEDIA_VOLUME) может быть пустым, только если в
#     базе нет ни одной живой строки files (новый сервер);
#   * архив не распаковывается; tar завершился с ошибкой — для постоянного
#     тома любой ненулевой код, для временного (BACKUP_TRANSIENT_VOLUMES) —
#     код 2 и выше;
#   * в архиве постоянного тома файлов меньше, чем было в томе перед
#     архивацией (файл пропал во время копирования);
#   * ключ документа в files повторяется, неоднозначен или хэш не SHA-256;
#   * хоть один документ, на который ссылается база, отсутствует в архиве
#     тома документов или его SHA-256 не совпадает с files.checksum_sha256.
#     Сверяется КАЖДЫЙ документ: число файлов ничего не доказывает — в томе
#     могут лежать чужие файлы, а нужного может не быть.
#
# Для временного тома (выгрузки удаляет само приложение) пропажа файла во
# время копирования и код tar 1 — не отказ, а строка «ВНИМАНИЕ» в журнале
# и строка warning в манифесте.
#
# Коды выхода:
#   0 — копия создана (и, если включено, отправлена и сверена снаружи);
#   1 — копия НЕ создана;
#   75 — копия создана и сохранена ЛОКАЛЬНО, но во внешнее хранилище НЕ
#       отправлена; отправка повторится при следующем запуске, а ротация
#       такую копию не удаляет. 75 — EX_TEMPFAIL («временный сбой»):
#       systemd показывает его как status=75/TEMPFAIL.
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

sent_dir="$BACKUP_DIR/.remote-sent"
list_sets() { ls -1 "$BACKUP_DIR" | grep -E '^20[0-9]{6}T[0-9]{6}Z$' | sort || true; }
unsent_sets() {
    [ "$BACKUP_REMOTE_ENABLED" = "true" ] || return 0
    local s
    for s in $(list_sets); do [ -e "$sent_dir/$s" ] || echo "$s"; done
}
free_mb() { df -Pk "$BACKUP_DIR" | awk 'NR == 2 {print int($4 / 1024)}'; }
used_pct() { df -Pk "$BACKUP_DIR" | awk 'NR == 2 {gsub("%", "", $5); print $5}'; }

# --- 0. Место ------------------------------------------------------------------
# Неотправленные копии ротация не удаляет, поэтому при долгой недоступности
# внешнего хранилища диск заполняется. Копия не начинается, если места мало:
# лучше явный отказ сегодня, чем заполненный диск под базой завтра.
avail="$(free_mb)"
if [ -n "$avail" ] && [ "$avail" -lt "$BACKUP_MIN_FREE_MB" ]; then
    die "на разделе $BACKUP_DIR свободно $avail МБ, нужно не меньше $BACKUP_MIN_FREE_MB (BACKUP_MIN_FREE_MB) — копия не начата. Копий, ждущих отправки наружу: $(unsent_sets | grep -c . || true)"
fi

work="$BACKUP_DIR/.partial-$stamp"
final="$BACKUP_DIR/$stamp"
remote_dump="/tmp/humotech-backup-$stamp.dump"
mkdir -p "$work"

cleanup() {
    local rc=$?
    docker exec "$PG_CONTAINER" rm -f "$remote_dump" >/dev/null 2>&1 || true
    # Каталог .partial существует, только пока копия не готова. После
    # переименования ненулевой код бывает лишь из-за отправки наружу — о ней
    # сообщает сама отправка, а готовая локальная копия остаётся.
    if [ $rc -ne 0 ] && [ -d "$work" ]; then
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
# Список документов берётся в той же транзакции, то есть из того же снимка.
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
SELECT format('SELECT %L, storage_key, checksum_sha256 FROM public.files WHERE deleted_at IS NULL AND storage_provider = %L ORDER BY storage_key', 'document', 'local')
 WHERE to_regclass('public.files') IS NOT NULL
\gexec
SELECT format('SELECT %L, %L, count(*) FROM public.files WHERE deleted_at IS NULL AND storage_provider <> %L', 'meta', 'files_other_provider', 'local')
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

# Ключи и хэши документов — в отдельный файл, который шифруется вместе с
# дампом. В открытом manifest.tsv остаются только числа.
{ grep -E $'^document\t' "$work/manifest.tsv" || true; } | cut -f2,3 > "$work/documents.tsv"
{ grep -vE $'^document\t' "$work/manifest.tsv" || true; } > "$work/manifest.tmp"
mv "$work/manifest.tmp" "$work/manifest.tsv"
tables=$(grep -c '^table' "$work/manifest.tsv" || true)
log "база: дамп $(du -h "$work/db.dump" | cut -f1), таблиц в манифесте: $tables"

other="$(awk -F'\t' '$1 == "meta" && $2 == "files_other_provider" {print $3}' "$work/manifest.tsv")"
if [ -n "$other" ] && [ "$other" != 0 ]; then
    log "ВНИМАНИЕ: $other живых файлов хранятся не в локальном хранилище — в эту копию они не входят"
    printf 'warning\tfiles_other_provider\t%s\n' "$other" >> "$work/manifest.tsv"
fi

# Ключ документа должен однозначно указывать на один путь в томе: только
# безопасные символы, без «.», «..» и пустых частей пути, без повторов.
awk -F'\t' '
    function bad(why) { n++; if (shown < 5) { print why ": " $1; shown++ } }
    {
        if (NF != 2) { bad("строка не из двух полей"); next }
        if ($1 !~ /^[A-Za-z0-9._-]+(\/[A-Za-z0-9._-]+)*$/) { bad("недопустимый ключ"); next }
        parts = split($1, seg, "/")
        for (i = 1; i <= parts; i++) if (seg[i] == "." || seg[i] == "..") { bad("неоднозначный ключ"); next }
        if ($2 !~ /^[0-9a-f]{64}$/) { bad("хэш не SHA-256"); next }
        if (seen[$1]++) { bad("ключ повторяется"); next }
    }
    END { printf "итог\t%d\n", n }
' "$work/documents.tsv" > "$work/documents.keys"
bad_keys="$(awk -F'\t' '$1 == "итог" {print $2}' "$work/documents.keys")"
if [ "$bad_keys" != 0 ]; then
    grep -v $'^итог\t' "$work/documents.keys" | sed 's/^/    /' >&2
    die "документы базы: $bad_keys записей files с недопустимым, неоднозначным или повторяющимся ключом — копия не создаётся"
fi
rm -f "$work/documents.keys"
docs="$(grep -c . "$work/documents.tsv" || true)"

# --- 2. Тома с файлами ------------------------------------------------------
# Для каждого тома: сколько файлов в нём сейчас → архив (код tar
# проверяется) → архив распакован во вспомогательном контейнере и
# пересчитан по SHA-256. Любой сбой — die.
helper() { docker run --rm --network none "$@"; }
# Живые строки files из того же снимка, что и дамп (пусто — таблицы нет).
live="$(awk -F'\t' '$1 == "meta" && $2 == "files_live" {print $3}' "$work/manifest.tsv")"
for i in "${!VOL_KEYS[@]}"; do
    key="${VOL_KEYS[$i]}" vol="${VOL_NAMES[$i]}"
    transient=0
    in_list "$key" "$BACKUP_TRANSIENT_VOLUMES" && transient=1
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

    tar_rc=0
    helper -v "$vol:/src:ro" "$BACKUP_HELPER_IMAGE" tar -C /src -czf - . \
        > "$work/volume-$key.tar.gz" 2> "$work/.tar-$key.err" || tar_rc=$?
    if [ "$tar_rc" -ne 0 ]; then
        sed 's/^/    tar: /' "$work/.tar-$key.err" | head -n 5 >&2
        # GNU tar: 1 — «файл изменился или удалён во время чтения», архив
        # цел; 2 и выше — фатальная ошибка.
        if [ "$transient" = 1 ] && [ "$tar_rc" -eq 1 ]; then
            log "ВНИМАНИЕ: том $key ($vol, временный): файлы менялись во время копирования (tar, код 1) — приложение удаляет выгрузки; архив проверяется дальше"
            printf 'warning\ttar_changed\t%s\n' "$key" >> "$work/manifest.tsv"
        else
            die "том $key ($vol): архивация завершилась с ошибкой (tar, код $tar_rc)"
        fi
    fi
    rm -f "$work/.tar-$key.err"

    helper -i "$BACKUP_HELPER_IMAGE" sh -c 'set -e; mkdir /x; tar -xzf - -C /x; cd /x; find . -type f -exec sha256sum {} +' \
        < "$work/volume-$key.tar.gz" > "$work/volume-$key.sha256" \
        || die "том $key ($vol): архив не распаковывается — копия повреждена"
    files="$(grep -c . "$work/volume-$key.sha256" || true)"
    if [ "$present" -gt 0 ] && [ "$files" -eq 0 ]; then
        die "том $key ($vol): в томе $present файлов, в архиве — ни одного"
    fi
    if [ "$files" -lt "$present" ]; then
        if [ "$transient" = 1 ]; then
            log "ВНИМАНИЕ: том $key ($vol, временный): перед архивацией файлов $present, в архиве $files — $((present - files)) удалено во время копирования"
            printf 'warning\tvolume_shrunk\t%s\t%s\t%s\n' "$key" "$present" "$files" >> "$work/manifest.tsv"
        else
            die "том $key ($vol): перед архивацией файлов $present, в архиве $files — файлы пропали во время копирования, копия не создаётся"
        fi
    elif [ "$files" -gt "$present" ]; then
        log "том $key: файлов было $present, в архиве $files (добавились во время копирования)"
    fi
    printf 'volume\t%s\t%s\t%s\n' "$key" "$vol" "$files" >> "$work/manifest.tsv"
    log "том $key ($vol): файлов $files, архив $(du -h "$work/volume-$key.tar.gz" | cut -f1)"
done

# --- 3. Документы базы — каждый по ключу и SHA-256 ------------------------------
media_sums="$work/volume-$BACKUP_MEDIA_VOLUME.sha256"
[ -f "$media_sums" ] || die "том с документами $BACKUP_MEDIA_VOLUME не входит в список копируемых томов"
# sha256sum пишет «<64 знака>  ./<путь>»; путь начинается с 67-го символа.
# Строки с экранированием (имя с «\» или переводом строки) начинаются с «\»
# и документом быть не могут: такие ключи отсеяны выше.
# Файл определяется по имени, а не по NR == FNR: при пустом списке сумм
# (пустой том) NR == FNR истинно и для второго файла — документы тогда не
# проверялись бы вовсе.
awk -F'\t' '
    FILENAME == ARGV[1] { if (substr($0, 1, 1) != "\\") sum[substr($0, 67)] = substr($0, 1, 64); next }
    {
        path = "./" $1
        if (!(path in sum)) { missing++; if (shown < 5) { print "нет в архиве: " $1; shown++ } }
        else if (sum[path] != $2) { changed++; if (shown < 5) { print "другое содержимое: " $1; shown++ } }
    }
    END { printf "итог\t%d\t%d\n", missing, changed }
' "$media_sums" "$work/documents.tsv" > "$work/documents.check"
read -r missing changed < <(awk -F'\t' '$1 == "итог" {print $2, $3}' "$work/documents.check")
if [ "$missing" -gt 0 ] || [ "$changed" -gt 0 ]; then
    grep -v $'^итог\t' "$work/documents.check" | sed 's/^/    /' >&2
    die "документы базы: из $docs нет в копии $missing, с другим содержимым $changed — копия не создаётся"
fi
rm -f "$work/documents.check"
log "документы базы: все $docs найдены в копии тома $BACKUP_MEDIA_VOLUME, SHA-256 совпадают с files.checksum_sha256"
printf 'meta\tdocuments\t%s\n' "$docs" >> "$work/manifest.tsv"
printf 'meta\tmedia_volume\t%s\n' "$BACKUP_MEDIA_VOLUME" >> "$work/manifest.tsv"
printf 'meta\tstatus\tcomplete\n' >> "$work/manifest.tsv"

# --- 4. Шифрование и контрольные суммы -------------------------------------
suffix="$(enc_suffix)"
for f in "$work"/db.dump "$work"/volume-*.tar.gz "$work"/volume-*.sha256 "$work"/documents.tsv; do
    [ -e "$f" ] || continue
    encrypt_file "$f"
done
(
    cd "$work"
    for f in db.dump"$suffix" volume-*.tar.gz"$suffix" volume-*.sha256"$suffix" documents.tsv"$suffix" manifest.tsv; do
        [ -e "$f" ] && printf '%s  %s\n' "$(sha256_file "$f")" "$f"
    done
) > "$work/SHA256SUMS"
mv "$work" "$final"
log "копия готова: $final (шифрование: $BACKUP_ENCRYPTION)"

# --- 5. Ротация на сервере -------------------------------------------------
# Храним BACKUP_KEEP_DAILY последних копий и дополнительно первую копию
# каждого из BACKUP_KEEP_MONTHLY последних месяцев. Копию, которая ещё не
# ушла во внешнее хранилище, не удаляем никогда: она может быть единственной.
mapfile -t sets < <(list_sets | sort -r)
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
    [ -n "${keep[$s]:-}" ] && continue
    if [ "$BACKUP_REMOTE_ENABLED" = "true" ] && [ ! -e "$sent_dir/$s" ]; then
        log "ротация: копия $s НЕ удалена — она ещё не отправлена во внешнее хранилище"
        continue
    fi
    rm -rf "${BACKUP_DIR:?}/$s"
    rm -f "$sent_dir/$s"
    log "ротация: удалена копия $s"
done
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '.partial-*' -mmin +1440 -exec rm -rf {} + 2>/dev/null || true

# --- 6. Копия вне сервера --------------------------------------------------
# Отправляется каждая локальная копия, ещё не отмеченная в .remote-sent/:
# сегодняшняя и те, что не ушли в прошлые запуски. Отметка ставится только
# после `rclone check`. Любая неудача — код 75 и явная строка в журнале;
# готовая локальная копия остаётся и ротацией не удаляется.
remote_rc=0
if [ "$BACKUP_REMOTE_ENABLED" = "true" ]; then
    need rclone
    [ -n "$BACKUP_RCLONE_REMOTE" ] || die "BACKUP_REMOTE_ENABLED=true, но не задан BACKUP_RCLONE_REMOTE"
    # Настройки хранилища rclone читает из RCLONE_CONFIG_<ИМЯ>_* (см.
    # backup.env.example): ключи доступа не лежат ни в одном файле скрипта.
    mkdir -p "$sent_dir"
    unsent=()
    for s in $(unsent_sets); do
        if rclone copy --immutable --checksum "$BACKUP_DIR/$s" "$BACKUP_RCLONE_REMOTE/$s" \
            && rclone check --one-way "$BACKUP_DIR/$s" "$BACKUP_RCLONE_REMOTE/$s"; then
            date -u +%Y-%m-%dT%H:%M:%SZ > "$sent_dir/$s"
            log "отправлено и сверено: $BACKUP_RCLONE_REMOTE/$s"
        else
            unsent+=("$s")
        fi
    done
    if [ "${#unsent[@]}" -gt 0 ]; then
        log "ОШИБКА: во внешнее хранилище НЕ отправлены копии: ${unsent[*]}. Локальная копия $stamp создана и сохранена в $final; отправка повторится при следующем запуске"
        remote_rc=75
    elif [ -n "$BACKUP_REMOTE_KEEP" ] && [ "$BACKUP_REMOTE_KEEP" != "0" ]; then
        if ! rclone delete --min-age "$BACKUP_REMOTE_KEEP" "$BACKUP_RCLONE_REMOTE"; then
            log "ОШИБКА: копии отправлены, но удалить во внешнем хранилище копии старше $BACKUP_REMOTE_KEEP не удалось"
            remote_rc=75
        fi
        rclone rmdirs --leave-root "$BACKUP_RCLONE_REMOTE" || true
    fi
else
    log "копия вне сервера выключена (BACKUP_REMOTE_ENABLED=false)"
fi

# --- 7. Предупреждения о месте ---------------------------------------------
pct="$(used_pct)"
if [ -n "$pct" ] && [ "$pct" -ge "$BACKUP_DISK_WARN_PERCENT" ]; then
    log "ВНИМАНИЕ: раздел $BACKUP_DIR заполнен на $pct% (порог $BACKUP_DISK_WARN_PERCENT%); при $BACKUP_MIN_FREE_MB МБ свободного места копии перестанут создаваться"
fi
pending="$(unsent_sets | grep -c . || true)"
if [ "$pending" -ge "$BACKUP_UNSENT_WARN" ] && [ "$pending" -gt 0 ]; then
    log "ВНИМАНИЕ: $pending копий ждут отправки во внешнее хранилище и не удаляются ротацией — диск будет заполняться, пока хранилище недоступно"
fi

[ "$remote_rc" -eq 0 ] || exit "$remote_rc"
echo "$final"
