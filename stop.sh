#!/usr/bin/env bash
# stop.sh — AI_Shop 一键停止（Java 微服务 + Python Agent）
# 用法: ./stop.sh [--middleware]
#   --middleware  同时停止 Docker 中间件（默认保留运行）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/AI_Shop-backend"
DEPLOY="$ROOT/deploy"
PIDS="$ROOT/run/pids"
RUNTIME_ENV="$ROOT/run/runtime.env"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }

STOP_MIDDLEWARE=false
for arg in "$@"; do
  case "$arg" in
    --middleware) STOP_MIDDLEWARE=true ;;
    *) warn "未知参数: $arg"; exit 1 ;;
  esac
done

mkdir -p "$PIDS"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$ROOT/run/start-stop.lock"
  flock -n 9 || { warn "另一个 start.sh/stop.sh 正在运行"; exit 1; }
fi

# shellcheck source=deploy/service-process-registry.sh
source "$DEPLOY/service-process-registry.sh"

# Use the same Compose interpolation values selected by start.sh.  The file is
# local-only and contains no shell code beyond generated KEY=value assignments.
if [[ -r "$RUNTIME_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV"
  set +a
fi

stop_svc() {
  local name=$1
  local pidfile="$PIDS/$name.pid"
  if [[ ! -f "$pidfile" ]]; then
    warn "$name: 无 PID 文件，跳过"
    return 0
  fi
  if ! is_running "$name"; then
    return 0
  fi
  load_pid_record "$name"
  local pid=$SERVICE_PID
  kill -TERM "$pid" 2>/dev/null || true
  local i=0
  while pid_record_is_current "$name" && [[ $i -lt 15 ]]; do
    sleep 1; i=$((i + 1))
  done
  # Re-check both PID and start time before SIGKILL so PID reuse cannot kill a newcomer.
  if pid_record_is_current "$name"; then
    warn "$name: SIGTERM 超时，强制 SIGKILL"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  clear_pid_record "$name"
  info "已停止 $name (pid=$pid)"
}

# 停止顺序：前端 → worker → agent/MCP → 业务服务 → gateway（逆启动顺序）
for svc in admin-web web agent-worker agent mcp admin search coupon pay order cart stock product user gateway; do
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
