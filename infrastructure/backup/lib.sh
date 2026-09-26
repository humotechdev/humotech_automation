# Общие функции скриптов резервного копирования HUMOTECH.
#
# Подключается из backup.sh и restore-test.sh, сам по себе не запускается.
# Ни одна функция не печатает значения секретов: в журнал попадают только
# имена переменных, пути и числа.

# Git Bash на Windows переписывает аргументы вида /tmp/x в C:/.../tmp/x —
# в том числе пути ВНУТРИ контейнера. На Linux переменная ни на что не влияет.
export MSYS_NO_PATHCONV=1

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${LOG_TAG:-backup}" "$*" >&2; }
die() { log "ОШИБКА: $*"; exit 1; }

# Настройки: сначала файл (по умолчанию backup.env рядом со скриптом), затем
# окружение процесса. Файл читается как shell, поэтому он должен
# принадлежать root и иметь права 600 — как и любой файл с ключами.
load_config() {
    local dir config
    dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
    config="${BACKUP_CONFIG:-$dir/backup.env}"
    if [ -f "$config" ]; then
        set -a
        # shellcheck disable=SC1090
        . "$config"
        set +a
    fi

    # Источник задаётся одним из двух способов.
    # 1) Проект Docker Compose (тестовый сервер, deploy/test-server):
    #    контейнер базы и тома находятся по меткам, которые ставит сам
    #    Compose (com.docker.compose.project/.service/.volume), — без
    #    угадывания формата имён «<проект>_<том>» или «<проект>-<сервис>-1».
    : "${BACKUP_COMPOSE_PROJECT:=}"
    : "${BACKUP_PG_SERVICE:=postgres}"
    : "${BACKUP_COMPOSE_VOLUMES:=private_media private_exports}"
    # 2) Явные имена (локальный стенд infrastructure/docker).
    : "${BACKUP_PG_CONTAINER:=humotech_postgres}"
    : "${BACKUP_PG_USER:=humotech}"
    : "${BACKUP_PG_DB:=humotech_django}"
    : "${BACKUP_VOLUMES:=docker_private_media docker_private_exports}"
    # Тома, которым разрешено быть пустыми (логические имена: ключ тома
    # compose или имя тома в режиме 2). Остальной пустой том — ошибка.
    : "${BACKUP_ALLOW_EMPTY_VOLUMES:=}"
    # Том с документами: в нём файлов не меньше, чем живых строк в таблице
    # files. Пусто — первый из списка томов.
    : "${BACKUP_MEDIA_VOLUME:=}"
    : "${BACKUP_DIR:=/var/backups/humotech}"
    : "${BACKUP_KEEP_DAILY:=14}"
    : "${BACKUP_KEEP_MONTHLY:=6}"
    : "${BACKUP_ENCRYPTION:=age}"
    : "${BACKUP_AGE_RECIPIENT:=}"
    : "${BACKUP_AGE_RECIPIENTS_FILE:=}"
    : "${BACKUP_AGE_IDENTITY_FILE:=}"
    : "${BACKUP_GPG_RECIPIENT:=}"
    : "${BACKUP_REMOTE_ENABLED:=false}"
    : "${BACKUP_RCLONE_REMOTE:=}"
    : "${BACKUP_REMOTE_KEEP:=90d}"
    : "${BACKUP_HELPER_IMAGE:=pgvector/pgvector:pg18}"
    : "${BACKUP_RESTORE_IMAGE:=pgvector/pgvector:pg18}"
    : "${BACKUP_VERIFY_APP_IMAGE:=}"
    : "${BACKUP_VERIFY_SETTINGS:=config.settings.test}"

    # Имена уходят в команды psql/pg_dump. Проверяем, а не экранируем:
    # здесь не бывает законных имён с пробелами и кавычками.
    local name
    for name in BACKUP_PG_CONTAINER BACKUP_PG_USER BACKUP_PG_DB BACKUP_PG_SERVICE; do
        [[ "${!name}" =~ ^[A-Za-z0-9_.-]+$ ]] || die "$name содержит недопустимые символы"
    done
    [ -z "$BACKUP_COMPOSE_PROJECT" ] || [[ "$BACKUP_COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] \
        || die "BACKUP_COMPOSE_PROJECT: недопустимое имя проекта"
    for name in $BACKUP_VOLUMES $BACKUP_COMPOSE_VOLUMES $BACKUP_ALLOW_EMPTY_VOLUMES $BACKUP_MEDIA_VOLUME; do
        [[ "$name" =~ ^[A-Za-z0-9_.-]+$ ]] || die "недопустимое имя тома в настройках"
    done
    case "$BACKUP_ENCRYPTION" in age|gpg|none) ;; *) die "BACKUP_ENCRYPTION: age, gpg или none" ;; esac
}

# resolve_sources — находит контейнер базы и тома и проверяет, что они
# есть. Заполняет PG_CONTAINER, VOL_KEYS[] (логические имена) и VOL_NAMES[]
# (настоящие имена томов Docker). Любая неясность — ошибка: копия, в
# которую молча не попал том с документами, хуже, чем отсутствие копии.
resolve_sources() {
    PG_CONTAINER="" VOL_KEYS=() VOL_NAMES=()
    local ids n key names state
    if [ -n "$BACKUP_COMPOSE_PROJECT" ]; then
        local p="label=com.docker.compose.project=$BACKUP_COMPOSE_PROJECT"
        ids="$(docker ps -q --filter "$p" --filter "label=com.docker.compose.service=$BACKUP_PG_SERVICE")"
        n="$(printf '%s\n' "$ids" | grep -c . || true)"
        if [ "$n" -eq 0 ]; then
            if docker ps -aq --filter "$p" --filter "label=com.docker.compose.service=$BACKUP_PG_SERVICE" | grep -q .; then
                die "контейнер сервиса $BACKUP_PG_SERVICE проекта $BACKUP_COMPOSE_PROJECT есть, но не запущен"
            fi
            die "контейнер сервиса $BACKUP_PG_SERVICE проекта $BACKUP_COMPOSE_PROJECT не найден (проверьте BACKUP_COMPOSE_PROJECT: docker compose ls)"
        fi
        [ "$n" -eq 1 ] || die "у сервиса $BACKUP_PG_SERVICE проекта $BACKUP_COMPOSE_PROJECT запущено контейнеров: $n, ожидался один"
        PG_CONTAINER="$(docker inspect -f '{{.Name}}' "$ids")"
        PG_CONTAINER="${PG_CONTAINER#/}"
        for key in $BACKUP_COMPOSE_VOLUMES; do
            names="$(docker volume ls -q --filter "$p" --filter "label=com.docker.compose.volume=$key")"
            n="$(printf '%s\n' "$names" | grep -c . || true)"
            [ "$n" -eq 0 ] && die "том $key проекта $BACKUP_COMPOSE_PROJECT НЕ НАЙДЕН — копия не создаётся"
            [ "$n" -eq 1 ] || die "томов $key в проекте $BACKUP_COMPOSE_PROJECT: $n, ожидался один"
            VOL_KEYS+=("$key"); VOL_NAMES+=("$names")
        done
    else
        state="$(docker inspect -f '{{.State.Running}}' "$BACKUP_PG_CONTAINER" 2>/dev/null || true)"
        [ -n "$state" ] || die "контейнер $BACKUP_PG_CONTAINER не найден"
        [ "$state" = "true" ] || die "контейнер $BACKUP_PG_CONTAINER есть, но не запущен"
        PG_CONTAINER="$BACKUP_PG_CONTAINER"
        for key in $BACKUP_VOLUMES; do
            docker volume inspect "$key" >/dev/null 2>&1 || die "том $key НЕ НАЙДЕН — копия не создаётся"
            VOL_KEYS+=("$key"); VOL_NAMES+=("$key")
        done
    fi
    [ "${#VOL_KEYS[@]}" -gt 0 ] || die "не задано ни одного тома с файлами"
    [ -n "$BACKUP_MEDIA_VOLUME" ] || BACKUP_MEDIA_VOLUME="${VOL_KEYS[0]}"
}

# in_list <слово> <список через пробел>
in_list() { case " $2 " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

need() { command -v "$1" >/dev/null 2>&1 || die "не найдена программа: $1"; }

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# Суффикс зашифрованного файла для выбранного способа.
enc_suffix() {
    case "$BACKUP_ENCRYPTION" in age) echo ".age" ;; gpg) echo ".gpg" ;; none) echo "" ;; esac
}

# encrypt_file <plain> — шифрует открытым ключом получателя и удаляет
# открытый файл. На сервере лежит только ПУБЛИЧНЫЙ ключ: расшифровать
# собственную копию сервер не может, и её кража с сервера ничего не даёт.
encrypt_file() {
    local plain="$1"
    case "$BACKUP_ENCRYPTION" in
        age)
            need age
            local args=()
            [ -n "$BACKUP_AGE_RECIPIENT" ] && args+=(-r "$BACKUP_AGE_RECIPIENT")
            [ -n "$BACKUP_AGE_RECIPIENTS_FILE" ] && args+=(-R "$BACKUP_AGE_RECIPIENTS_FILE")
            [ "${#args[@]}" -gt 0 ] || die "не задан BACKUP_AGE_RECIPIENT или BACKUP_AGE_RECIPIENTS_FILE"
            age "${args[@]}" -o "$plain.age" "$plain"
            ;;
        gpg)
            need gpg
            [ -n "$BACKUP_GPG_RECIPIENT" ] || die "не задан BACKUP_GPG_RECIPIENT"
            gpg --batch --yes --quiet --trust-model always \
                --encrypt --recipient "$BACKUP_GPG_RECIPIENT" \
                --output "$plain.gpg" "$plain"
            ;;
        none) return 0 ;;
    esac
    rm -f "$plain"
}

# decrypt_file <encrypted> <out> — только для проверки восстановления.
# Закрытый ключ нужен здесь и больше нигде; см. docs/security/backups.md.
decrypt_file() {
    local src="$1" out="$2"
    case "$src" in
        *.age)
            need age
            [ -n "$BACKUP_AGE_IDENTITY_FILE" ] || die "для .age нужен BACKUP_AGE_IDENTITY_FILE"
            age -d -i "$BACKUP_AGE_IDENTITY_FILE" -o "$out" "$src"
            ;;
        *.gpg)
            need gpg
            gpg --batch --yes --quiet --decrypt --output "$out" "$src"
            ;;
        *) cp "$src" "$out" ;;
    esac
}
