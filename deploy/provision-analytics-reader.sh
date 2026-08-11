#!/usr/bin/env bash
set -euo pipefail

: "${MYSQL_ADMIN_USER:?MYSQL_ADMIN_USER is required}"
: "${MYSQL_ADMIN_PASSWORD:?MYSQL_ADMIN_PASSWORD is required}"
: "${ANALYTICS_MYSQL_PASSWORD:?ANALYTICS_MYSQL_PASSWORD is required}"

analytics_user="${ANALYTICS_MYSQL_USER:-analytics_reader}"
analytics_host="${ANALYTICS_MYSQL_ALLOWED_HOST:-%}"
if [[ ! "$analytics_user" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "ANALYTICS_MYSQL_USER contains unsupported characters" >&2
  exit 2
fi
if [[ ! "$analytics_host" =~ ^[%A-Za-z0-9._:-]+$ ]]; then
  echo "ANALYTICS_MYSQL_ALLOWED_HOST contains unsupported characters" >&2
  exit 2
fi
if [[ -n "${MYSQL_CONTAINER:-}" && ! "$MYSQL_CONTAINER" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "MYSQL_CONTAINER contains unsupported characters" >&2
  exit 2
fi

escaped_password=${ANALYTICS_MYSQL_PASSWORD//\'/\'\'}

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

view_count=$(run_mysql --batch --skip-column-names --execute="
    SELECT COUNT(*)
      FROM information_schema.views
     WHERE table_schema='aishop_admin'
       AND table_name IN (
         'analytics_sales_daily',
         'analytics_product_sales_daily',
         'analytics_inventory_risk',
         'analytics_agent_quality_daily',
         'analytics_tool_quality_daily',
         'analytics_recommendation_funnel_daily',
         'analytics_recommendation_quality_daily',
         'analytics_offer_quality_daily',
         'analytics_fulfillment_after_sales_daily',
         'analytics_inventory_forecast'
       );
  ")
if [[ "$view_count" != "10" ]]; then
  echo "ten governed analytics views must exist before provisioning the reader" >&2
  exit 3
fi

run_mysql --execute="
    SET SESSION sql_mode='NO_BACKSLASH_ESCAPES';
    CREATE USER IF NOT EXISTS '${analytics_user}'@'${analytics_host}' IDENTIFIED BY '${escaped_password}';
    ALTER USER '${analytics_user}'@'${analytics_host}' IDENTIFIED BY '${escaped_password}';
    REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_sales_daily TO '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_product_sales_daily TO '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_inventory_risk TO '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_agent_quality_daily TO '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_tool_quality_daily TO '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_recommendation_funnel_daily TO '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_recommendation_quality_daily TO '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_offer_quality_daily TO '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_fulfillment_after_sales_daily TO '${analytics_user}'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_inventory_forecast TO '${analytics_user}'@'${analytics_host}';
  "

echo "${analytics_user} provisioned with SELECT on governed views only"
