#!/usr/bin/env bash
# Initialize the MySQL schemas required before Nacos and Seata can start.
# All SQL files are idempotent and may be applied again on an existing local stack.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SQL_ROOT="$ROOT/sql"
MYSQL_CONTAINER="${AISHOP_MYSQL_CONTAINER:-aishop-mysql}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-root}"

info() { printf '[mysql-init] %s\n' "$*"; }
die() { printf '[mysql-init] %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"

for sql_file in \
  00_create_databases.sql \
  00b_nacos_seata_databases.sql \
  14_nacos.sql \
  15_seata.sql \
  16_seata_undo_log.sql; do
  [[ -r "$SQL_ROOT/$sql_file" ]] || die "missing SQL file: $SQL_ROOT/$sql_file"
done

wait_mysql() {
  local status attempt
  for attempt in $(seq 1 60); do
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "$MYSQL_CONTAINER" 2>/dev/null || true)
    if [[ "$status" == "healthy" ]]; then
      return 0
    fi
    if [[ "$status" == "unhealthy" || "$status" == "exited" || "$status" == "dead" ]]; then
      docker logs --tail 80 "$MYSQL_CONTAINER" >&2 || true
      die "MySQL container is $status"
    fi
    sleep 2
  done
  docker logs --tail 80 "$MYSQL_CONTAINER" >&2 || true
  die "MySQL did not become healthy within 120 seconds"
}

apply_sql() {
  local file=$1
  info "applying $file"
  docker exec -i "$MYSQL_CONTAINER" mysql \
    --default-character-set=utf8mb4 \
    -uroot "-p${MYSQL_ROOT_PASSWORD}" <"$SQL_ROOT/$file"
}

wait_mysql

apply_sql 00_create_databases.sql
apply_sql 00b_nacos_seata_databases.sql
apply_sql 14_nacos.sql
apply_sql 15_seata.sql
apply_sql 16_seata_undo_log.sql

info "Nacos, Seata, business databases, and undo_log schemas are ready"
