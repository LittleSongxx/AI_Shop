#!/usr/bin/env bash
# 本地/服务器一键提示：请按模块分别启动 JAR（示例）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/AI_Shop-backend"
JAVA_OPTS="${JAVA_OPTS:--Xms256m -Xmx512m}"

echo "Ensure env loaded (AISHOP_PRODUCTION_READY, AISHOP_INTERNAL_TOKEN, ...)"
echo "Migrate order/product/stock services and Agent before Admin creates analytics views."
echo "Agent schema only: cd $BACKEND/AI_Shop-agent && python scripts/migrate.py"
echo "Then provision the Flyway identity and start Admin to create the governed views."
echo "Finally provision analytics_reader and restart Agent; DataAnalyst is enabled by default."
echo "Starting gateway..."
# java $JAVA_OPTS -jar "$BACKEND/AI_Shop-gateway/target/aishop-gateway-1.0.0.jar" &
echo "Then start: user product stock cart coupon order pay search; migrate Agent; then admin"
echo "Agent API: cd $BACKEND/AI_Shop-agent && uvicorn app.main:app --host 0.0.0.0 --port 7050"
echo "Agent worker: cd $BACKEND/AI_Shop-agent && python -m app.worker"
echo "See deploy/上线检查清单.md for full checklist."
