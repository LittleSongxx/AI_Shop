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
if [[ "${flyway_user,,}" == "root" ]]; then
  echo "Flyway must not use the MySQL root identity" >&2
  exit 2
fi
if [[ -n "${MYSQL_USER:-}" && "$flyway_user" == "$MYSQL_USER" ]]; then
  echo "Flyway and business services must use separate MySQL identities" >&2
  exit 2
fi
if [[ ! "$flyway_host" =~ ^[%A-Za-z0-9._:-]+$ ]]; then
  echo "FLYWAY_ALLOWED_HOST contains unsupported characters" >&2
  exit 2
fi
if [[ -n "${MYSQL_CONTAINER:-}" && ! "$MYSQL_CONTAINER" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "MYSQL_CONTAINER contains unsupported characters" >&2
  exit 2
fi

escaped_password=${FLYWAY_PASSWORD//\'/\'\'}

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
  CREATE USER IF NOT EXISTS '${flyway_user}'@'${flyway_host}' IDENTIFIED BY '${escaped_password}';
  ALTER USER '${flyway_user}'@'${flyway_host}' IDENTIFIED BY '${escaped_password}';
  REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_admin.* TO '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_agent.* TO '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_cart.* TO '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_coupon.* TO '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_order.* TO '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_pay.* TO '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_product.* TO '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_search.* TO '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_stock.* TO '${flyway_user}'@'${flyway_host}';
  GRANT ALL PRIVILEGES ON aishop_user.* TO '${flyway_user}'@'${flyway_host}';
"

echo "${flyway_user} provisioned for schema migrations only"
