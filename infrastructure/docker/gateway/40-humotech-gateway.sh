#!/bin/sh
# Списки доступа шлюза из переменных окружения.
#
# Запускается до старта nginx: `command` в compose вызывает его через `sh`
# и затем штатный /docker-entrypoint.sh (так не нужен бит исполнения).
# Пишет два файла, которые nginx.conf подключает по маске:
#
#   /etc/nginx/humotech/real-ip.conf       set_real_ip_from ...;
#   /etc/nginx/humotech/admin-allow.conf   allow ...;
#
# GATEWAY_TRUSTED_PROXIES — адреса или сети ВНЕШНЕГО прокси, которому шлюз
#   верит в X-Forwarded-For (ngrok, Caddy). Только от них берётся адрес
#   клиента; всем остальным заголовок не помогает. Пусто — не верим никому,
#   адрес клиента = адрес соединения.
#
# GATEWAY_ADMIN_ALLOW — адреса или сети, с которых открыт /admin/.
#   Пусто — /admin/ закрыт для всех (403). Сравнивается с адресом клиента
#   ПОСЛЕ разбора X-Forwarded-For доверенного прокси.
#
# Разделитель — запятая или пробел. Кривое значение роняет старт
# контейнера: молча пропущенная строка списка доступа хуже упавшего шлюза.
#
# Нет скрипта (или он не исполняемый) — нет и файлов: маска в nginx.conf
# ничего не находит, /admin/ закрыт, X-Forwarded-For не читается. То есть
# отказ здесь всегда в закрытую сторону.

set -eu

out=/etc/nginx/humotech
mkdir -p "$out"
rm -f "$out"/real-ip.conf "$out"/admin-allow.conf

# $1 — имя переменной (для сообщения), $2 — значение, $3 — директива, $4 — файл.
emit() {
    name=$1
    value=$2
    directive=$3
    file=$4
    list=$(printf '%s' "$value" | tr ',' ' ')
    for item in $list; do
        case "$item" in
            *[!0-9A-Fa-f.:/]*)
                echo "40-humotech-gateway: $name: недопустимое значение «$item»" >&2
                exit 1
                ;;
        esac
        printf '%s %s;\n' "$directive" "$item" >> "$file"
    done
}

emit GATEWAY_TRUSTED_PROXIES "${GATEWAY_TRUSTED_PROXIES:-}" set_real_ip_from "$out/real-ip.conf"
emit GATEWAY_ADMIN_ALLOW "${GATEWAY_ADMIN_ALLOW:-}" allow "$out/admin-allow.conf"

if [ -s "$out/admin-allow.conf" ]; then
    echo "40-humotech-gateway: /admin/ открыт для: $(tr '\n' ' ' < "$out/admin-allow.conf")"
else
    echo "40-humotech-gateway: /admin/ закрыт (GATEWAY_ADMIN_ALLOW пуст)"
fi
if [ ! -s "$out/real-ip.conf" ]; then
    echo "40-humotech-gateway: доверенных прокси нет, X-Forwarded-For не читается"
fi
