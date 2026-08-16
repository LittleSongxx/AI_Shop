#!/usr/bin/env bash
set -euo pipefail

: "${MYSQL_ADMIN_USER:?MYSQL_ADMIN_USER is required}"
: "${MYSQL_ADMIN_PASSWORD:?MYSQL_ADMIN_PASSWORD is required}"
: "${NACOS_MYSQL_USER:?NACOS_MYSQL_USER is required}"
: "${NACOS_MYSQL_PASSWORD:?NACOS_MYSQL_PASSWORD is required}"
: "${SEATA_MYSQL_USER:?SEATA_MYSQL_USER is required}"
: "${SEATA_MYSQL_PASSWORD:?SEATA_MYSQL_PASSWORD is required}"

allowed_host="${MYSQL_ALLOWED_HOST:-%}"

validate_identity() {
  local label=$1 user=$2
  if [[ ! "$user" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "$label contains unsupported characters" >&2
    exit 2
  fi
  if [[ "${user,,}" == "root" ]]; then
    echo "$label must not use the MySQL root identity" >&2
    exit 2
  fi
}

validate_identity NACOS_MYSQL_USER "$NACOS_MYSQL_USER"
validate_identity SEATA_MYSQL_USER "$SEATA_MYSQL_USER"
if [[ "$NACOS_MYSQL_USER" == "$SEATA_MYSQL_USER" ]]; then
  echo "Nacos and Seata must use separate MySQL identities" >&2
  exit 2
fi
if [[ ! "$allowed_host" =~ ^[%A-Za-z0-9._:-]+$ ]]; then
  echo "MYSQL_ALLOWED_HOST contains unsupported characters" >&2
  exit 2
fi
if [[ -n "${MYSQL_CONTAINER:-}" && ! "$MYSQL_CONTAINER" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "MYSQL_CONTAINER contains unsupported characters" >&2
  exit 2
fi

nacos_password=${NACOS_MYSQL_PASSWORD//\'/\'\'}
seata_password=${SEATA_MYSQL_PASSWORD//\'/\'\'}

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
  CREATE USER IF NOT EXISTS '${NACOS_MYSQL_USER}'@'${allowed_host}' IDENTIFIED BY '${nacos_password}';
  ALTER USER '${NACOS_MYSQL_USER}'@'${allowed_host}' IDENTIFIED BY '${nacos_password}';
  REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${NACOS_MYSQL_USER}'@'${allowed_host}';
  GRANT ALL PRIVILEGES ON nacos.* TO '${NACOS_MYSQL_USER}'@'${allowed_host}';

  CREATE USER IF NOT EXISTS '${SEATA_MYSQL_USER}'@'${allowed_host}' IDENTIFIED BY '${seata_password}';
  ALTER USER '${SEATA_MYSQL_USER}'@'${allowed_host}' IDENTIFIED BY '${seata_password}';
  REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${SEATA_MYSQL_USER}'@'${allowed_host}';
  GRANT ALL PRIVILEGES ON seata.* TO '${SEATA_MYSQL_USER}'@'${allowed_host}';
"

echo "Nacos and Seata MySQL identities provisioned"
