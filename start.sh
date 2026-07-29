#!/usr/bin/env bash
# start.sh — AI_Shop 一键启动（中间件 + Java 微服务 + Python Agent）
# 用法: ./start.sh [--build] [--middleware-only]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/AI_Shop-backend"
DEPLOY="$ROOT/deploy"
PIDS="$ROOT/run/pids"
LOGS="$ROOT/run/logs"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
die()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

# ── 参数解析 ─────────────────────────────────────────────────────────
BUILD=false; MIDDLEWARE_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --build)           BUILD=true ;;
    --middleware-only) MIDDLEWARE_ONLY=true ;;
  esac
done

# ── 环境变量（与 docker-compose.middleware.yml 宿主机端口对齐） ───────
export REDIS_HOST=127.0.0.1
export REDIS_PORT=6380
export RABBIT_HOST=127.0.0.1
export RABBIT_PORT=5673
export RABBIT_USER=aishop
export RABBIT_PASSWORD=aishop
export RABBIT_VHOST=/
export NACOS_ADDR=127.0.0.1:8848
export SEATA_SERVER_ADDR=127.0.0.1:8092
export JAVA_OPTS="${JAVA_OPTS:--Xms256m -Xmx512m}"

mkdir -p "$PIDS" "$LOGS"

# ── 工具函数 ─────────────────────────────────────────────────────────
wait_port() {
  local host=$1 port=$2 label=$3 timeout=${4:-90}
  local end=$((SECONDS + timeout))
  echo -n "  等待 $label..."
  while [[ $SECONDS -lt $end ]]; do
    if (echo >/dev/tcp/"$host"/"$port") 2>/dev/null; then
      echo " 就绪"; return 0
    fi
    sleep 2; echo -n "."
  done
  die "$label 在 ${timeout}s 内未就绪"
}

is_running() {
  local pidfile="$PIDS/$1.pid"
  [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

start_java() {
  local name=$1 jar=$2 port=$3
  if is_running "$name"; then
    warn "$name 已运行 (pid=$(cat "$PIDS/$name.pid"))，跳过"
    return 0
  fi
  [[ -f "$jar" ]] || die "JAR 不存在: $jar\n  请先运行: cd $BACKEND && mvn -q package -DskipTests"
  java $JAVA_OPTS -jar "$jar" >"$LOGS/$name.log" 2>&1 &
  echo $! >"$PIDS/$name.pid"
  info "启动 $name  port=$port  pid=$!  log: run/logs/$name.log"
}

# ── 1. 可选：Maven 构建 ──────────────────────────────────────────────
if $BUILD; then
  info "Maven 构建中（-DskipTests）..."
  cd "$BACKEND" && mvn -q package -DskipTests
  cd "$ROOT"
  info "构建完成"
fi

# ── 2. 中间件（Docker） ───────────────────────────────────────────────
info "启动 Docker 中间件..."
docker compose -f "$DEPLOY/docker-compose.middleware.yml" up -d
wait_port 127.0.0.1 3306 "MySQL"  60
wait_port 127.0.0.1 8848 "Nacos" 120
wait_port 127.0.0.1 8092 "Seata"  90

$MIDDLEWARE_ONLY && { info "仅启动中间件，退出"; exit 0; }

# ── 3. Java 微服务 ────────────────────────────────────────────────────
info "启动 Java 微服务..."

# Gateway 优先，其余服务注册到 Nacos 后需要它做路由
start_java gateway "$BACKEND/AI_Shop-gateway/target/aishop-gateway-1.0.0.jar" 8080
wait_port 127.0.0.1 8080 "gateway" 120

# 业务服务并行拉起
start_java user    "$BACKEND/AI_Shop-user/app/target/aishop-user-1.0.0.jar"       8105
start_java product "$BACKEND/AI_Shop-product/app/target/aishop-product-1.0.0.jar" 8099
start_java stock   "$BACKEND/AI_Shop-stock/app/target/aishop-stock-1.0.0.jar"     8102
start_java cart    "$BACKEND/AI_Shop-cart/app/target/aishop-cart-1.0.0.jar"       8084
start_java order   "$BACKEND/AI_Shop-order/app/target/aishop-order-1.0.0.jar"     8093
start_java pay     "$BACKEND/AI_Shop-pay/app/target/aishop-pay-1.0.0.jar"         8096
start_java coupon  "$BACKEND/AI_Shop-coupon/app/target/aishop-coupon-1.0.0.jar"   8087
start_java search  "$BACKEND/AI_Shop-search/target/aishop-search-1.0.0.jar"       8108
start_java admin   "$BACKEND/AI_Shop-admin/target/aishop-admin-1.0.0.jar"         8111

# ── 4. Python Agent ───────────────────────────────────────────────────
PYTHON="$(conda info --base 2>/dev/null)/envs/shop/bin/python"
if is_running "agent"; then
  warn "agent 已运行 (pid=$(cat "$PIDS/agent.pid"))，跳过"
elif [[ ! -x "$PYTHON" ]]; then
  die "conda env 'shop' 未找到: $PYTHON\n  请先: conda create -n shop python=3.12 && pip install -e '$BACKEND/AI_Shop-agent[dev]'"
else
  (cd "$BACKEND/AI_Shop-agent" && exec "$PYTHON" -m uvicorn app.main:app \
      --host 0.0.0.0 --port 7050 \
      >"$LOGS/agent.log" 2>&1) &
  echo $! >"$PIDS/agent.pid"
  info "启动 agent  port=7050  pid=$!  log: run/logs/agent.log"
fi

# ── 汇总 ─────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "全部服务已启动"
printf "  %-12s %s\n" "Gateway"   "http://127.0.0.1:8080"
printf "  %-12s %s\n" "Agent"     "http://127.0.0.1:7050"
printf "  %-12s %s\n" "Nacos"     "http://127.0.0.1:8848/nacos"
printf "  %-12s %s\n" "RabbitMQ"  "http://127.0.0.1:15673  (aishop/aishop)"
printf "  %-12s %s\n" "Sentinel"  "http://127.0.0.1:8858"
printf "  %-12s %s\n" "Seata控制台" "http://127.0.0.1:7092"
printf "  %-12s %s\n" "日志目录"   "$ROOT/run/logs/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
