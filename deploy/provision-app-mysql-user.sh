#!/usr/bin/env bash
set -euo pipefail

: "${MYSQL_ADMIN_USER:?MYSQL_ADMIN_USER is required}"
: "${MYSQL_ADMIN_PASSWORD:?MYSQL_ADMIN_PASSWORD is required}"
: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"

app_user=$MYSQL_USER
app_host="${MYSQL_ALLOWED_HOST:-%}"

if [[ ! "$app_user" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "MYSQL_USER contains unsupported characters" >&2
  exit 2
fi
if [[ "${app_user,,}" == "root" ]]; then
  echo "business services must not use the MySQL root identity" >&2
  exit 2
fi
if [[ ! "$app_host" =~ ^[%A-Za-z0-9._:-]+$ ]]; then
  echo "MYSQL_ALLOWED_HOST contains unsupported characters" >&2
  exit 2
fi
if [[ -n "${MYSQL_CONTAINER:-}" && ! "$MYSQL_CONTAINER" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "MYSQL_CONTAINER contains unsupported characters" >&2
  exit 2
fi

escaped_password=${MYSQL_PASSWORD//\'/\'\'}

run_mysql() {
  if [[ -n "${MYSQL_CONTAINER:-}" ]]; then
    docker exec -e MYSQL_PWD="$MYSQL_ADMIN_PASSWORD" "$MYSQL_CONTAINER" mysql \
      --protocol=TCP \
      --host=127.0.0.1 \
      --port=3306 \
      --user="$MYSQL_ADMIN_USER" \
      "$@"
  else
    MYSQL_PWD="$MYSQL_ADMIN_PASSWORD" mysql \
      --protocol=TCP \
      --host="${MYSQL_HOST:-127.0.0.1}" \
      --port="${MYSQL_PORT:-3306}" \
      --user="$MYSQL_ADMIN_USER" \
      "$@"
  fi
}

run_mysql --execute="
  SET SESSION sql_mode='NO_BACKSLASH_ESCAPES';
  CREATE USER IF NOT EXISTS '${app_user}'@'${app_host}' IDENTIFIED BY '${escaped_password}';
  ALTER USER '${app_user}'@'${app_host}' IDENTIFIED BY '${escaped_password}';
  REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_admin.* TO '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_agent.* TO '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_cart.* TO '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_coupon.* TO '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_order.* TO '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_pay.* TO '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_product.* TO '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_search.* TO '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_stock.* TO '${app_user}'@'${app_host}';
  GRANT SELECT, INSERT, UPDATE, DELETE ON aishop_user.* TO '${app_user}'@'${app_host}';
"

echo "${app_user} provisioned with runtime DML privileges on AI Shop business schemas"
