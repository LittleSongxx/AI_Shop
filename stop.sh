#!/usr/bin/env bash
# stop.sh — AI_Shop 一键停止（Java 微服务 + Python Agent）
# 用法: ./stop.sh [--middleware]
#   --middleware  同时停止 Docker 中间件（默认保留运行）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$ROOT/deploy"
PIDS="$ROOT/run/pids"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }

STOP_MIDDLEWARE=false
for arg in "$@"; do
  [[ "$arg" == "--middleware" ]] && STOP_MIDDLEWARE=true
done

stop_svc() {
  local name=$1
  local pidfile="$PIDS/$name.pid"
  if [[ ! -f "$pidfile" ]]; then
    warn "$name: 无 PID 文件，跳过"
    return 0
  fi
  local pid
  pid=$(cat "$pidfile")
  if ! kill -0 "$pid" 2>/dev/null; then
    warn "$name: 进程 $pid 已不在，清理 PID 文件"
    rm -f "$pidfile"; return 0
  fi
  kill -TERM "$pid" 2>/dev/null || true
  local i=0
  while kill -0 "$pid" 2>/dev/null && [[ $i -lt 15 ]]; do
    sleep 1; i=$((i + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    warn "$name: SIGTERM 超时，强制 SIGKILL"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
  info "已停止 $name (pid=$pid)"
}

# 停止顺序：agent → 业务服务 → gateway（逆启动顺序）
for svc in agent admin search coupon pay order cart stock product user gateway; do
  stop_svc "$svc"
done

if $STOP_MIDDLEWARE; then
  info "停止 Docker 中间件..."
  docker compose -f "$DEPLOY/docker-compose.middleware.yml" down
  info "中间件已停止"
else
  info "Docker 中间件保持运行（./stop.sh --middleware 可一并停止）"
fi

echo ""
info "全部完成"
