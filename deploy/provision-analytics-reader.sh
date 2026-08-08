#!/usr/bin/env bash
set -euo pipefail

: "${MYSQL_ADMIN_USER:?MYSQL_ADMIN_USER is required}"
: "${MYSQL_ADMIN_PASSWORD:?MYSQL_ADMIN_PASSWORD is required}"
: "${ANALYTICS_MYSQL_PASSWORD:?ANALYTICS_MYSQL_PASSWORD is required}"

analytics_host="${ANALYTICS_MYSQL_ALLOWED_HOST:-%}"
if [[ ! "$analytics_host" =~ ^[%A-Za-z0-9._:-]+$ ]]; then
  echo "ANALYTICS_MYSQL_ALLOWED_HOST contains unsupported characters" >&2
  exit 2
fi

escaped_password=${ANALYTICS_MYSQL_PASSWORD//\'/\'\'}
view_count=$(MYSQL_PWD="$MYSQL_ADMIN_PASSWORD" mysql \
  --protocol=TCP \
  --host="${MYSQL_HOST:-127.0.0.1}" \
  --port="${MYSQL_PORT:-3306}" \
  --user="$MYSQL_ADMIN_USER" \
  --batch --skip-column-names \
  --execute="
    SELECT COUNT(*)
      FROM information_schema.views
     WHERE table_schema='aishop_admin'
       AND table_name IN (
         'analytics_sales_daily',
         'analytics_product_sales_daily',
         'analytics_inventory_risk',
         'analytics_agent_quality_daily',
         'analytics_tool_quality_daily'
       );
  ")
if [[ "$view_count" != "5" ]]; then
  echo "five governed analytics views must exist before provisioning the reader" >&2
  exit 3
fi

MYSQL_PWD="$MYSQL_ADMIN_PASSWORD" mysql \
  --protocol=TCP \
  --host="${MYSQL_HOST:-127.0.0.1}" \
  --port="${MYSQL_PORT:-3306}" \
  --user="$MYSQL_ADMIN_USER" \
  --execute="
    SET SESSION sql_mode='NO_BACKSLASH_ESCAPES';
    CREATE USER IF NOT EXISTS 'analytics_reader'@'${analytics_host}' IDENTIFIED BY '${escaped_password}';
    ALTER USER 'analytics_reader'@'${analytics_host}' IDENTIFIED BY '${escaped_password}';
    REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'analytics_reader'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_sales_daily TO 'analytics_reader'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_product_sales_daily TO 'analytics_reader'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_inventory_risk TO 'analytics_reader'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_agent_quality_daily TO 'analytics_reader'@'${analytics_host}';
    GRANT SELECT ON aishop_admin.analytics_tool_quality_daily TO 'analytics_reader'@'${analytics_host}';
  "

echo "analytics_reader provisioned with SELECT on governed views only"
