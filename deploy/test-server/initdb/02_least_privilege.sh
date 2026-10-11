#!/bin/sh
# Разделение прав PostgreSQL. Запускается автоматически при создании тома
# данных (docker-entrypoint-initdb.d) ПОСЛЕ 01_databases.sql.
#
# Отдельным shell-обёрткой, а не .sql: только shell читает переменные
# окружения с паролями ролей. Пароли не логируются и не сохраняются на
# диск — передаются psql переменной и подставляются format(%L).
set -eu

: "${POSTGRES_OWNER_PASSWORD:?нужен POSTGRES_OWNER_PASSWORD}"
: "${POSTGRES_APP_PASSWORD:?нужен POSTGRES_APP_PASSWORD}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_password="$POSTGRES_OWNER_PASSWORD" \
  -v app_password="$POSTGRES_APP_PASSWORD" \
  -f /docker-entrypoint-initdb.d/sql/02_least_privilege.sql

echo "02_least_privilege: роли humotech_owner и humotech_app созданы."
