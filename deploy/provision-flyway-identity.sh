#!/usr/bin/env bash
set -euo pipefail

: "${MYSQL_ADMIN_USER:?MYSQL_ADMIN_USER is required}"
: "${MYSQL_ADMIN_PASSWORD:?MYSQL_ADMIN_PASSWORD is required}"
: "${FLYWAY_PASSWORD:?FLYWAY_PASSWORD is required}"

flyway_user="${FLYWAY_USER:-aishop_flyway}"
flyway_host="${FLYWAY_ALLOWED_HOST:-%}"
if [[ ! "$flyway_user" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "FLYWAY_USER contains unsupported characters" >&2
  exit 2
fi
if [[ ! "$flyway_host" =~ ^[%A-Za-z0-9._:-]+$ ]]; then
  echo "FLYWAY_ALLOWED_HOST contains unsupported characters" >&2
  exit 2
fi

escaped_password=${FLYWAY_PASSWORD//\'/\'\'}
MYSQL_PWD="$MYSQL_ADMIN_PASSWORD" mysql \
  --protocol=TCP \
  --host="${MYSQL_HOST:-127.0.0.1}" \
  --port="${MYSQL_PORT:-3306}" \
  --user="$MYSQL_ADMIN_USER" \
  --execute="
    SET SESSION sql_mode='NO_BACKSLASH_ESCAPES';
    CREATE USER IF NOT EXISTS '${flyway_user}'@'${flyway_host}' IDENTIFIED BY '${escaped_password}';
    ALTER USER '${flyway_user}'@'${flyway_host}' IDENTIFIED BY '${escaped_password}';
    REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${flyway_user}'@'${flyway_host}';
    GRANT ALL PRIVILEGES ON aishop_admin.* TO '${flyway_user}'@'${flyway_host}';
    GRANT SELECT ON aishop_order.* TO '${flyway_user}'@'${flyway_host}';
    GRANT SELECT ON aishop_product.* TO '${flyway_user}'@'${flyway_host}';
    GRANT SELECT ON aishop_stock.* TO '${flyway_user}'@'${flyway_host}';
    GRANT SELECT ON aishop_agent.* TO '${flyway_user}'@'${flyway_host}';
  "

echo "${flyway_user} provisioned for Admin migrations and governed view definer access"
