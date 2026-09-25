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

    : "${BACKUP_PG_CONTAINER:=humotech_postgres}"
    : "${BACKUP_PG_USER:=humotech}"
    : "${BACKUP_PG_DB:=humotech_django}"
    : "${BACKUP_VOLUMES:=docker_private_media docker_private_exports}"
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
    for name in BACKUP_PG_CONTAINER BACKUP_PG_USER BACKUP_PG_DB; do
        [[ "${!name}" =~ ^[A-Za-z0-9_.-]+$ ]] || die "$name содержит недопустимые символы"
    done
    for name in $BACKUP_VOLUMES; do
        [[ "$name" =~ ^[A-Za-z0-9_.-]+$ ]] || die "BACKUP_VOLUMES: недопустимое имя тома"
    done
    case "$BACKUP_ENCRYPTION" in age|gpg|none) ;; *) die "BACKUP_ENCRYPTION: age, gpg или none" ;; esac
}

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
