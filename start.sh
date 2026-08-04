#!/usr/bin/env bash
# AI_Shop local stack: Docker middleware, Java services, Agent, MCP, and Vite frontends.
# Usage: ./start.sh [--build] [--middleware-only]
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/AI_Shop-backend"
DEPLOY="$ROOT/deploy"
FRONT="$ROOT/AI_Shop-front"
COMPOSE_FILE="$DEPLOY/docker-compose.middleware.yml"
PIDS="$ROOT/run/pids"
LOGS="$ROOT/run/logs"
RUNTIME_ENV="$ROOT/run/runtime.env"
DEFAULT_PROJECT_FOLDER="$ROOT/run/data/aishop/upload"
SIMLECT_SYNC_TOOL="$BACKEND/data/tools/sync_simlect_catalog.py"
SIMLECT_CATALOG="$BACKEND/data/simlect_catalog/catalog.json"
SIMLECT_VERSION="$BACKEND/data/simlect_catalog/VERSION"
SIMLECT_SEED="$BACKEND/data/02_simlect_catalog_seed.sql"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die() { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

BUILD=false
MIDDLEWARE_ONLY=false
CATALOG_CHANGED=false
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=true ;;
    --middleware-only) MIDDLEWARE_ONLY=true ;;
    *) die "未知参数: $arg（可用: --build, --middleware-only）" ;;
  esac
done

mkdir -p "$PIDS" "$LOGS"

# start/stop share a lock so PID records and port assignments cannot race.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$ROOT/run/start-stop.lock"
  flock -n 9 || die "另一个 start.sh/stop.sh 正在运行"
fi

# shellcheck source=deploy/service-process-registry.sh
source "$DEPLOY/service-process-registry.sh"

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"
}

validate_port() {
  local value=$1 label=$2 numeric
  [[ "$value" =~ ^[0-9]+$ ]] || die "$label 必须是 1..65535 的端口，当前值: $value"
  numeric=$((10#$value))
  ((numeric >= 1 && numeric <= 65535)) \
    || die "$label 必须是 1..65535 的端口，当前值: $value"
}

port_is_listening() {
  local port=$1
  if command -v ss >/dev/null 2>&1; then
    ss -H -ltn "sport = :$port" 2>/dev/null | grep -q .
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null
  fi
}

show_service_log() {
  local name=$1 log="$LOGS/$1.log"
  [[ -f "$log" ]] || return 0
  warn "$name 最近的日志："
  tail -n 40 "$log" >&2 || true
}

service_startup_failed() {
  local name=$1 log="$LOGS/$1.log"
  [[ -f "$log" ]] || return 1
  grep -Eq "APPLICATION FAILED TO START|Web server failed to start|Port [0-9]+ is already in use|Address already in use" "$log"
}

show_container_log() {
  local name=$1
  warn "$name 最近的容器日志："
  docker logs --tail 80 "$name" >&2 || true
}

wait_port() {
  local port=$1 label=$2 timeout=${3:-120}
  local end=$((SECONDS + timeout))
  echo -n "  等待 $label..."
  while [[ $SECONDS -lt $end ]]; do
    if port_is_listening "$port"; then
      echo " 就绪"
      return 0
    fi
    sleep 2
    echo -n "."
  done
  echo " 超时"
  die "$label 没有在 ${timeout}s 内监听端口 $port"
}

wait_http() {
  local url=$1 label=$2 timeout=${3:-120}
  local end=$((SECONDS + timeout))
  echo -n "  检查 $label..."
  while [[ $SECONDS -lt $end ]]; do
    if curl --noproxy '*' -fsS --max-time 5 "$url" >/dev/null 2>&1; then
      echo " 就绪"
      return 0
    fi
    sleep 2
    echo -n "."
  done
  echo " 超时"
  die "$label 没有在 ${timeout}s 内通过健康检查: $url"
}

wait_container_healthy() {
  local container=$1 label=$2 timeout=${3:-180}
  local end=$((SECONDS + timeout)) status
  echo -n "  等待 $label..."
  while [[ $SECONDS -lt $end ]]; do
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "$container" 2>/dev/null || true)
    if [[ "$status" == "healthy" || "$status" == "running" ]]; then
      echo " 就绪"
      return 0
    fi
    if [[ "$status" == "unhealthy" || "$status" == "exited" || "$status" == "dead" ]]; then
      echo " 失败"
      show_container_log "$container"
      die "$label 容器状态为 $status"
    fi
    sleep 2
    echo -n "."
  done
  echo " 超时"
  show_container_log "$container"
  die "$label 没有在 ${timeout}s 内就绪"
}

ensure_host_ports() {
  local service=$1 label=$2 container=$3 port
  shift 3
  local missing=false
  local -a ports=("$@")

  # Docker Desktop can retain a container's port-binding metadata while its
  # forwarding proxy is gone (for example after a Compose file edit).  An
  # in-container health check does not detect that state, so verify every
  # host-facing port before starting processes outside Docker.
  for port in "${ports[@]}"; do
    local ready=false
    for _ in {1..10}; do
      if port_is_listening "$port"; then
        ready=true
        break
      fi
      sleep 1
    done
    if ! $ready; then
      missing=true
      warn "$label 容器未发布宿主端口 $port，将强制重建 $service"
    fi
  done

  if $missing; then
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate "$service"
    wait_container_healthy "$container" "$label" 180
    for port in "${ports[@]}"; do
      wait_port "$port" "$label" 60
    done
  fi
}

wait_managed_service() {
  local name=$1 port=$2 label=$3 timeout=${4:-150}
  local end=$((SECONDS + timeout)) identity_end=$((SECONDS + 5))
  echo -n "  等待 $label..."
  while [[ $SECONDS -lt $end ]]; do
    if service_startup_failed "$name"; then
      stop_managed_service_for_restart "$name" 10 || true
      echo " 失败"
      show_service_log "$name"
      die "$label 启动失败（详见 run/logs/$name.log）"
    fi
    if ! pid_record_is_current "$name"; then
      clear_pid_record "$name"
      echo " 失败"
      show_service_log "$name"
      die "$label 进程在监听端口 $port 前退出"
    fi
    # Python processes briefly transition from their launcher to exec().
    if ! pid_command_matches_service "$name"; then
      if [[ $SECONDS -lt $identity_end ]]; then
        sleep 1
        echo -n "."
        continue
      fi
      clear_pid_record "$name"
      echo " 失败"
      show_service_log "$name"
      die "$label 的 PID 未转换为预期进程"
    fi
    load_pid_record "$name"
    if port_owned_by_pid "$port" "$SERVICE_PID"; then
      echo " 就绪"
      return 0
    fi
    sleep 1
    echo -n "."
  done
  echo " 超时"
  show_service_log "$name"
  die "$label 进程没有在 ${timeout}s 内监听端口 $port"
}

project_container_uses_port() {
  local port=$1 container mappings
  for container in aishop-mysql aishop-redis aishop-rabbitmq aishop-nacos aishop-es aishop-sentinel aishop-seata; do
    [[ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" == "true" ]] || continue
    mappings=$(docker port "$container" 2>/dev/null || true)
    if grep -Eq ":${port}([[:space:]]|$)" <<<"$mappings"; then
      return 0
    fi
  done
  return 1
}

managed_service_uses_port() {
  local port=$1 name
  for name in gateway user product stock cart order pay coupon search admin mcp agent agent-worker web admin-web; do
    if pid_record_is_current "$name" && pid_command_matches_service "$name"; then
      load_pid_record "$name"
      if port_owned_by_pid "$port" "$SERVICE_PID"; then
        return 0
      fi
    fi
  done
  return 1
}

declare -A RESERVED_PORTS=()

port_can_be_used() {
  local port=$1
  [[ -z "${RESERVED_PORTS[$port]+x}" ]] || return 1
  if ! port_is_listening "$port"; then
    return 0
  fi
  project_container_uses_port "$port" || managed_service_uses_port "$port"
}

assign_port() {
  local variable=$1 default_port=$2 label=$3 requested candidate
  requested="${!variable:-$default_port}"
  validate_port "$requested" "$label"
  candidate=$((10#$requested))
  while ((candidate <= 65535)); do
    if port_can_be_used "$candidate"; then
      printf -v "$variable" '%s' "$candidate"
      export "$variable"
      RESERVED_PORTS["$candidate"]=1
      if [[ "$candidate" != "$requested" ]]; then
        warn "$label 默认端口 $requested 已被占用，自动改用 $candidate"
      fi
      return 0
    fi
    candidate=$((candidate + 1))
  done
  die "$label 没有可用端口"
}

assign_nacos_ports() {
  local requested candidate grpc expected_grpc
  requested="${NACOS_PORT:-8848}"
  validate_port "$requested" "Nacos HTTP"
  candidate=$((10#$requested))
  expected_grpc=$((candidate + 1000))
  if [[ -n "${NACOS_GRPC_PORT:-}" && "$NACOS_GRPC_PORT" != "$expected_grpc" ]]; then
    warn "Nacos gRPC 必须与 HTTP 端口保持 +1000，忽略 NACOS_GRPC_PORT=$NACOS_GRPC_PORT"
  fi
  while ((candidate <= 64535)); do
    grpc=$((candidate + 1000))
    if port_can_be_used "$candidate" && port_can_be_used "$grpc"; then
      NACOS_PORT=$candidate
      NACOS_GRPC_PORT=$grpc
      export NACOS_PORT NACOS_GRPC_PORT
      RESERVED_PORTS["$candidate"]=1
      RESERVED_PORTS["$grpc"]=1
      if [[ "$candidate" != "$requested" ]]; then
        warn "Nacos 默认端口 $requested/$expected_grpc 已被占用，自动改用 $candidate/$grpc"
      fi
      return 0
    fi
    candidate=$((candidate + 1))
  done
  die "Nacos 没有可用的 HTTP/gRPC 端口对"
}

PORT_VARIABLES=(
  MYSQL_PORT REDIS_PORT RABBIT_PORT RABBIT_MANAGEMENT_PORT
  NACOS_PORT NACOS_GRPC_PORT ES_PORT SENTINEL_PORT SEATA_PORT SEATA_CONSOLE_PORT
  GATEWAY_PORT USER_PORT PRODUCT_PORT STOCK_PORT CART_PORT ORDER_PORT PAY_PORT COUPON_PORT SEARCH_PORT ADMIN_PORT
  AGENT_PORT AGENT_WORKER_METRICS_PORT MCP_PORT WEB_PORT ADMIN_WEB_PORT
)
RUNTIME_VARIABLES=("${PORT_VARIABLES[@]}" AISHOP_INTERNAL_TOKEN)

# A caller-supplied value wins over the saved local runtime assignment.
declare -A CALLER_VALUES=()
for variable in "${RUNTIME_VARIABLES[@]}" MYSQL_HOST MYSQL_USER MYSQL_PASSWORD MYSQL_ROOT_PASSWORD SEATA_IP; do
  if [[ -v "$variable" ]]; then
    CALLER_VALUES["$variable"]="${!variable}"
  fi
done
if [[ -r "$RUNTIME_ENV" ]]; then
  # The file is created below with shell-escaped values and mode 0600.
  set -a
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV"
  set +a
fi
for variable in "${!CALLER_VALUES[@]}"; do
  printf -v "$variable" '%s' "${CALLER_VALUES[$variable]}"
  export "$variable"
done

prepare_ports() {
  assign_port MYSQL_PORT 3306 "MySQL"
  assign_port REDIS_PORT 6380 "Redis"
  assign_port RABBIT_PORT 5673 "RabbitMQ"
  assign_port RABBIT_MANAGEMENT_PORT 15673 "RabbitMQ 管理台"
  assign_nacos_ports
  assign_port ES_PORT 9200 "Elasticsearch"
  assign_port SENTINEL_PORT 8858 "Sentinel"
  assign_port SEATA_PORT 8092 "Seata TC"
  assign_port SEATA_CONSOLE_PORT 7092 "Seata 控制台"

  assign_port GATEWAY_PORT 8080 "Gateway"
  assign_port USER_PORT 8105 "User 服务"
  assign_port PRODUCT_PORT 8099 "Product 服务"
  assign_port STOCK_PORT 8102 "Stock 服务"
  assign_port CART_PORT 8084 "Cart 服务"
  assign_port ORDER_PORT 8093 "Order 服务"
  assign_port PAY_PORT 8096 "Pay 服务"
  assign_port COUPON_PORT 8087 "Coupon 服务"
  assign_port SEARCH_PORT 8108 "Search 服务"
  assign_port ADMIN_PORT 8111 "Admin 服务"
  assign_port AGENT_PORT 7050 "Agent API"
  assign_port AGENT_WORKER_METRICS_PORT 7051 "Agent Worker 指标"
  assign_port MCP_PORT 7060 "MCP Server"
  assign_port WEB_PORT 6001 "商城前端"
  assign_port ADMIN_WEB_PORT 6002 "管理前端"
}

random_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
  fi
}

detect_host_ip() {
  local detected=""
  if command -v ip >/dev/null 2>&1; then
    detected=$(ip -4 route get 1.1.1.1 2>/dev/null \
      | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')
  fi
  if [[ -z "$detected" ]]; then
    detected=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  [[ "$detected" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "无法确定供 Seata 注册到 Nacos 的 WSL 宿主地址；请显式设置 SEATA_IP"
  printf '%s\n' "$detected"
}

write_runtime_env() {
  local temp="$RUNTIME_ENV.tmp.$$" variable
  umask 077
  : >"$temp"
  for variable in "${RUNTIME_VARIABLES[@]}"; do
    printf '%s=%q\n' "$variable" "${!variable}" >>"$temp"
  done
  mv -f "$temp" "$RUNTIME_ENV"
  chmod 600 "$RUNTIME_ENV"
}

prepare_environment() {
  prepare_ports

  export MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
  export MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-root}"
  export MYSQL_USER="${MYSQL_USER:-root}"
  export MYSQL_PASSWORD="${MYSQL_PASSWORD:-$MYSQL_ROOT_PASSWORD}"
  export REDIS_HOST="127.0.0.1"
  export RABBIT_HOST="127.0.0.1"
  export RABBIT_USER="${RABBIT_USER:-aishop}"
  export RABBIT_PASSWORD="${RABBIT_PASSWORD:-aishop}"
  export RABBIT_VHOST="${RABBIT_VHOST:-/}"
  export NACOS_ADDR="127.0.0.1:$NACOS_PORT"
  export SENTINEL_DASHBOARD="127.0.0.1:$SENTINEL_PORT"
  export SEATA_SERVER_ADDR="127.0.0.1:$SEATA_PORT"
  export SEATA_IP="${SEATA_IP:-$(detect_host_ip)}"
  export ES_URIS="http://127.0.0.1:$ES_PORT"
  export ES_HOSTS="$ES_URIS"
  export SPRING_ELASTICSEARCH_URIS="$ES_URIS"
  export RABBITMQ_URL="${RABBITMQ_URL:-amqp://$RABBIT_USER:$RABBIT_PASSWORD@127.0.0.1:$RABBIT_PORT/}"
  export PROJECT_FOLDER="${PROJECT_FOLDER:-$DEFAULT_PROJECT_FOLDER}"
  if [[ "$PROJECT_FOLDER" != /* ]]; then
    PROJECT_FOLDER="$ROOT/$PROJECT_FOLDER"
  fi
  PROJECT_FOLDER="${PROJECT_FOLDER%/}/"
  export PROJECT_FOLDER
  mkdir -p "${PROJECT_FOLDER}file"

  # Local service-to-service calls must never be routed through a developer's
  # global HTTP proxy.  Keep caller-provided exclusions and append the local
  # names used by this stack for both conventional variable spellings.
  local local_no_proxy="127.0.0.1,localhost,::1,mysql,redis,rabbitmq,nacos,elasticsearch,seata-server"
  export NO_PROXY="${NO_PROXY:+$NO_PROXY,}$local_no_proxy"
  export no_proxy="${no_proxy:+$no_proxy,}$local_no_proxy"

  export AGENT_HOST="127.0.0.1"
  export APP_HOST="0.0.0.0"
  export APP_PORT="$AGENT_PORT"
  export WORKER_METRICS_PORT="$AGENT_WORKER_METRICS_PORT"
  export AGENT_BASE_URL="http://127.0.0.1:$AGENT_PORT"
  export JAVA_WEB_URL="http://127.0.0.1:$GATEWAY_PORT"
  export FASTMCP_HOST="127.0.0.1"
  export FASTMCP_PORT="$MCP_PORT"
  export MCP_SERVER_URL="http://127.0.0.1:$MCP_PORT"
  export APP_ENV="${APP_ENV:-development}"
  export AISHOP_PRODUCTION_READY="${AISHOP_PRODUCTION_READY:-false}"
  # Ten Spring Boot processes share the WSL VM with existing user workloads.
  # Keep the local default conservative; callers may override JAVA_OPTS when RAM is available.
  export JAVA_OPTS="${JAVA_OPTS:--Xms64m -Xmx192m -XX:+UseSerialGC -XX:MaxMetaspaceSize=192m -XX:MaxDirectMemorySize=96m -Xss512k -XX:TieredStopAtLevel=1}"

  if [[ -z "${AISHOP_INTERNAL_TOKEN:-}" || "$AISHOP_INTERNAL_TOKEN" == "your-token" ]]; then
    AISHOP_INTERNAL_TOKEN=$(random_token)
  fi
  export AISHOP_INTERNAL_TOKEN
  write_runtime_env
}

resolve_python() {
  local conda_base=""
  if [[ -n "${AISHOP_PYTHON:-}" ]]; then
    PYTHON="$AISHOP_PYTHON"
  else
    if command -v conda >/dev/null 2>&1; then
      conda_base=$(conda info --base 2>/dev/null || true)
    fi
    if [[ -n "$conda_base" && -x "$conda_base/envs/shop/bin/python" ]]; then
      PYTHON="$conda_base/envs/shop/bin/python"
    elif [[ -x "$HOME/anaconda3/envs/shop/bin/python" ]]; then
      PYTHON="$HOME/anaconda3/envs/shop/bin/python"
    else
      PYTHON=""
    fi
  fi
  [[ -n "$PYTHON" && -x "$PYTHON" ]] \
    || die "conda 环境 shop 未找到；请用 AISHOP_PYTHON 指定 Python 解释器"
}

ensure_jars_or_build() {
  local jar missing=false
  for jar in \
    "$BACKEND/AI_Shop-gateway/target/aishop-gateway-1.0.0.jar" \
    "$BACKEND/AI_Shop-user/app/target/aishop-user-1.0.0.jar" \
    "$BACKEND/AI_Shop-product/app/target/aishop-product-1.0.0.jar" \
    "$BACKEND/AI_Shop-stock/app/target/aishop-stock-1.0.0.jar" \
    "$BACKEND/AI_Shop-cart/app/target/aishop-cart-1.0.0.jar" \
    "$BACKEND/AI_Shop-order/app/target/aishop-order-1.0.0.jar" \
    "$BACKEND/AI_Shop-pay/app/target/aishop-pay-1.0.0.jar" \
    "$BACKEND/AI_Shop-coupon/app/target/aishop-coupon-1.0.0.jar" \
    "$BACKEND/AI_Shop-search/target/aishop-search-1.0.0.jar" \
    "$BACKEND/AI_Shop-admin/target/aishop-admin-1.0.0.jar"; do
    [[ -f "$jar" ]] || missing=true
  done

  if $BUILD || $missing; then
    require_command mvn
    info "Maven 构建中（-DskipTests）..."
    (cd "$BACKEND" && mvn -q package -DskipTests)
    info "Maven 构建完成"
  fi
}

start_middleware() {
  info "启动 MySQL 并初始化元数据库..."
  docker compose -f "$COMPOSE_FILE" up -d mysql
  ensure_host_ports mysql "MySQL" aishop-mysql "$MYSQL_PORT"
  wait_container_healthy aishop-mysql "MySQL" 180
  "$DEPLOY/init-mysql-meta.sh"

  info "启动其余 Docker 中间件..."
  docker compose -f "$COMPOSE_FILE" up -d redis rabbitmq nacos elasticsearch sentinel seata-server
  ensure_host_ports redis "Redis" aishop-redis "$REDIS_PORT"
  ensure_host_ports rabbitmq "RabbitMQ" aishop-rabbitmq "$RABBIT_PORT" "$RABBIT_MANAGEMENT_PORT"
  ensure_host_ports nacos "Nacos" aishop-nacos "$NACOS_PORT" "$NACOS_GRPC_PORT"
  ensure_host_ports elasticsearch "Elasticsearch" aishop-es "$ES_PORT"
  ensure_host_ports sentinel "Sentinel" aishop-sentinel "$SENTINEL_PORT"
  ensure_host_ports seata-server "Seata TC" aishop-seata "$SEATA_PORT" "$SEATA_CONSOLE_PORT"
  wait_container_healthy aishop-redis "Redis" 120
  wait_container_healthy aishop-rabbitmq "RabbitMQ" 180
  wait_container_healthy aishop-nacos "Nacos" 240
  wait_container_healthy aishop-es "Elasticsearch" 300
  wait_port "$SENTINEL_PORT" "Sentinel" 120
  wait_port "$SEATA_PORT" "Seata TC" 180
  wait_http "http://127.0.0.1:$NACOS_PORT/nacos/" "Nacos HTTP" 60
}

ensure_port_free() {
  local port=$1 label=$2
  if port_is_listening "$port"; then
    die "$label 无法启动：端口 $port 在端口分配后又被其他进程占用"
  fi
}

ensure_memory_headroom() {
  local label=$1 minimum_mb=${AISHOP_MIN_AVAILABLE_MEMORY_MB:-1536}
  local available_kb
  [[ "$minimum_mb" =~ ^[0-9]+$ ]] \
    || die "AISHOP_MIN_AVAILABLE_MEMORY_MB 必须是非负整数，当前值: $minimum_mb"
  available_kb=$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)
  [[ "$available_kb" =~ ^[0-9]+$ ]] || return 0
  if ((available_kb < minimum_mb * 1024)); then
    die "$label 启动前仅剩 $((available_kb / 1024)) MiB 可用内存，低于安全线 ${minimum_mb} MiB；已停止继续启动以保护 WSL"
  fi
}

start_java() {
  local name=$1 jar=$2 port=$3
  if is_running "$name"; then
    load_pid_record "$name"
    warn "$name 已运行 (pid=$SERVICE_PID)，跳过拉起"
    return 0
  fi
  ensure_memory_headroom "$name"
  ensure_port_free "$port" "$name"
  [[ -f "$jar" ]] || die "JAR 不存在: $jar；请使用 ./start.sh --build"
  local -a java_opts
  read -r -a java_opts <<<"$JAVA_OPTS"
  if [[ "$name" == "gateway" ]]; then
    # The gateway starts before the business services.  The environment
    # overrides also protect an already-built JAR until source is rebuilt.
    SERVER_PORT="$port" AISHOP_MANAGED_SERVICE="$name" \
      SPRING_CLOUD_NACOS_DISCOVERY_WATCH_ENABLED="${SPRING_CLOUD_NACOS_DISCOVERY_WATCH_ENABLED:-true}" \
      SPRING_CLOUD_NACOS_DISCOVERY_NAMING_LOAD_CACHE_AT_START="${SPRING_CLOUD_NACOS_DISCOVERY_NAMING_LOAD_CACHE_AT_START:-true}" \
      setsid nohup java "${java_opts[@]}" -jar "$jar" \
      9>&- </dev/null >"$LOGS/$name.log" 2>&1 &
  else
    SERVER_PORT="$port" AISHOP_MANAGED_SERVICE="$name" \
      setsid nohup java "${java_opts[@]}" -jar "$jar" \
      9>&- </dev/null >"$LOGS/$name.log" 2>&1 &
  fi
  local pid=$!
  write_pid_record "$name" "$pid"
  info "拉起 $name  port=$port  pid=$pid  log: run/logs/$name.log"
}

start_mcp() {
  if is_running mcp; then
    load_pid_record mcp
    warn "mcp 已运行 (pid=$SERVICE_PID)，跳过拉起"
    return 0
  fi
  ensure_memory_headroom "MCP"
  ensure_port_free "$MCP_PORT" "MCP"
  (
    cd "$BACKEND/AI_Shop-agent"
    export AISHOP_MANAGED_SERVICE=mcp
    exec setsid nohup "$PYTHON" -m app.mcp_server
  ) 9>&- </dev/null >"$LOGS/mcp.log" 2>&1 &
  local pid=$!
  write_pid_record mcp "$pid"
  info "拉起 mcp  port=$MCP_PORT  pid=$pid  log: run/logs/mcp.log"
}

start_agent_api() {
  if is_running agent; then
    load_pid_record agent
    warn "agent 已运行 (pid=$SERVICE_PID)，跳过拉起"
    return 0
  fi
  ensure_memory_headroom "Agent API"
  ensure_port_free "$AGENT_PORT" "Agent API"
  (
    cd "$BACKEND/AI_Shop-agent"
    export AISHOP_MANAGED_SERVICE=agent
    exec setsid nohup "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "$AGENT_PORT"
  ) 9>&- </dev/null >"$LOGS/agent.log" 2>&1 &
  local pid=$!
  write_pid_record agent "$pid"
  info "拉起 agent  port=$AGENT_PORT  pid=$pid  log: run/logs/agent.log"
}

start_agent_worker() {
  if is_running agent-worker; then
    load_pid_record agent-worker
    warn "agent-worker 已运行 (pid=$SERVICE_PID)，跳过拉起"
    return 0
  fi
  ensure_memory_headroom "Agent Worker"
  ensure_port_free "$AGENT_WORKER_METRICS_PORT" "Agent Worker"
  (
    cd "$BACKEND/AI_Shop-agent"
    export AISHOP_MANAGED_SERVICE=agent-worker
    exec setsid nohup "$PYTHON" -m app.worker
  ) 9>&- </dev/null >"$LOGS/agent-worker.log" 2>&1 &
  local pid=$!
  write_pid_record agent-worker "$pid"
  info "拉起 agent-worker  metrics=$AGENT_WORKER_METRICS_PORT  pid=$pid  log: run/logs/agent-worker.log"
}

start_storefront() {
  if is_running web; then
    load_pid_record web
    warn "web 已运行 (pid=$SERVICE_PID)，跳过拉起"
    return 0
  fi
  ensure_memory_headroom "商城前端"
  [[ -f "$FRONT/AI_Shop-web/node_modules/vite/bin/vite.js" ]] \
    || die "商城前端依赖未安装：cd AI_Shop-front/AI_Shop-web && npm ci"
  ensure_port_free "$WEB_PORT" "商城前端"
  (
    cd "$FRONT/AI_Shop-web"
    export AISHOP_MANAGED_SERVICE=web
    export VITE_DEV_PORT="$WEB_PORT"
    export VITE_API_PROXY_TARGET="http://127.0.0.1:$GATEWAY_PORT"
    export VITE_AGENT_PROXY_TARGET="http://127.0.0.1:$AGENT_PORT"
    export VITE_WS_PROXY_TARGET="ws://127.0.0.1:$AGENT_PORT"
    exec setsid nohup node "$FRONT/AI_Shop-web/node_modules/vite/bin/vite.js" --host 0.0.0.0 --port "$WEB_PORT" --strictPort
  ) 9>&- </dev/null >"$LOGS/web.log" 2>&1 &
  local pid=$!
  write_pid_record web "$pid"
  info "拉起 web  port=$WEB_PORT  pid=$pid  log: run/logs/web.log"
}

start_admin_web() {
  if is_running admin-web; then
    load_pid_record admin-web
    warn "admin-web 已运行 (pid=$SERVICE_PID)，跳过拉起"
    return 0
  fi
  ensure_memory_headroom "管理前端"
  [[ -f "$FRONT/AI_Shop-admin/node_modules/vite/bin/vite.js" ]] \
    || die "管理前端依赖未安装：cd AI_Shop-front/AI_Shop-admin && npm ci"
  ensure_port_free "$ADMIN_WEB_PORT" "管理前端"
  (
    cd "$FRONT/AI_Shop-admin"
    export AISHOP_MANAGED_SERVICE=admin-web
    export VITE_ADMIN_DEV_PORT="$ADMIN_WEB_PORT"
    export VITE_ADMIN_API_PROXY_TARGET="http://127.0.0.1:$GATEWAY_PORT"
    export VITE_ADMIN_WS_PROXY_TARGET="ws://127.0.0.1:$GATEWAY_PORT"
    exec setsid nohup node "$FRONT/AI_Shop-admin/node_modules/vite/bin/vite.js" --host 0.0.0.0 --port "$ADMIN_WEB_PORT" --strictPort
  ) 9>&- </dev/null >"$LOGS/admin-web.log" 2>&1 &
  local pid=$!
  write_pid_record admin-web "$pid"
  info "拉起 admin-web  port=$ADMIN_WEB_PORT  pid=$pid  log: run/logs/admin-web.log"
}

mysql_query() {
  docker exec aishop-mysql mysql -N -B -uroot "-p${MYSQL_ROOT_PASSWORD}" "$@"
}

stop_managed_service_for_restart() {
  local name=$1 timeout=${2:-40} end pid
  if ! is_running "$name"; then
    return 0
  fi
  load_pid_record "$name"
  pid=$SERVICE_PID
  kill -TERM "$pid" 2>/dev/null || true
  end=$((SECONDS + timeout))
  while [[ $SECONDS -lt $end ]] && pid_record_is_current "$name"; do
    sleep 1
  done
  if pid_record_is_current "$name"; then
    warn "$name 优雅停止超时，发送 SIGKILL"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  clear_pid_record "$name"
  info "已停止 $name，等待按新目录重新启动"
}

install_catalog_assets() {
  [[ -r "$SIMLECT_SYNC_TOOL" && -r "$SIMLECT_CATALOG" && -r "$SIMLECT_VERSION" ]] \
    || die "Simlect 镜像元数据缺失，请先运行 data/tools/sync_simlect_catalog.py --sync"
  [[ -r "$SIMLECT_SEED" ]] || die "Simlect 镜像 SQL 缺失: $SIMLECT_SEED"

  info "校验并安装授权镜像商品图片..."
  "$PYTHON" "$SIMLECT_SYNC_TOOL" --check
  "$PYTHON" "$SIMLECT_SYNC_TOOL" --install-to "$PROJECT_FOLDER"
}

seed_product_data() {
  local catalog_version expected_count meta_exists installed_version="" installed_count="0"
  catalog_version=$(<"$SIMLECT_VERSION")
  expected_count=$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["productCount"])' \
    "$SIMLECT_CATALOG")
  [[ -n "$catalog_version" && "$expected_count" =~ ^[0-9]+$ ]] \
    || die "无法读取 Simlect 镜像版本或商品数"

  meta_exists=$(mysql_query -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='aishop_product' AND table_name='simlect_catalog_meta';" \
    | tr -d '[:space:]')
  if [[ "$meta_exists" == "1" ]]; then
    installed_version=$(mysql_query aishop_product -e \
      "SELECT COALESCE(MAX(catalog_version),'') FROM simlect_catalog_meta WHERE catalog_key='default';" \
      | tr -d '\r\n')
    installed_count=$(mysql_query aishop_product -e \
      "SELECT COUNT(*) FROM simlect_catalog_product;" | tr -d '[:space:]')
  fi

  if [[ "$installed_version" == "$catalog_version" && "$installed_count" == "$expected_count" ]]; then
    info "商品镜像已是 $catalog_version（$expected_count 件），跳过导入"
    CATALOG_CHANGED=false
    return 0
  fi

  info "导入授权商品镜像 $catalog_version（$expected_count 件）..."
  docker exec -i aishop-mysql mysql --default-character-set=utf8mb4 -uroot "-p${MYSQL_ROOT_PASSWORD}" aishop_product \
    <"$SIMLECT_SEED"

  installed_version=$(mysql_query aishop_product -e \
    "SELECT catalog_version FROM simlect_catalog_meta WHERE catalog_key='default';" | tr -d '\r\n')
  installed_count=$(mysql_query aishop_product -e \
    "SELECT COUNT(*) FROM simlect_catalog_product;" | tr -d '[:space:]')
  [[ "$installed_version" == "$catalog_version" && "$installed_count" == "$expected_count" ]] \
    || die "商品镜像导入后校验失败：version=$installed_version count=$installed_count"
  CATALOG_CHANGED=true
  info "商品镜像导入完成"
}

reset_catalog_indexes() {
  local index status
  for index in aishop-index aishop_vectorstore; do
    status=$(curl --noproxy '*' -sS --max-time 30 -o /dev/null -w '%{http_code}' \
      -X DELETE "$ES_URIS/$index")
    [[ "$status" == "200" || "$status" == "404" ]] \
      || die "删除 Elasticsearch 索引 $index 失败，HTTP $status"
  done
  info "已清理旧商品关键词/向量索引"
}

rebuild_catalog_search_index() {
  local response expected actual="0" end
  info "低负载重建商品关键词索引（不生成向量）..."
  response=$(curl --noproxy '*' -fsS --max-time 240 -X POST \
    -H "X-Internal-Token: $AISHOP_INTERNAL_TOKEN" \
    "http://127.0.0.1:$SEARCH_PORT/internal/search/tool/productData?includeVector=false") \
    || die "商品关键词索引重建请求失败"
  [[ "$response" == *'"code":200'* ]] || die "商品关键词索引重建返回异常: $response"

  expected=$(mysql_query aishop_product -e 'SELECT COUNT(*) FROM product_info WHERE status=1;' | tr -d '[:space:]')
  end=$((SECONDS + 60))
  while [[ $SECONDS -lt $end ]]; do
    response=$(curl --noproxy '*' -fsS --max-time 10 "$ES_URIS/aishop-index/_count" || true)
    actual=$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("count", -1))' <<<"$response" 2>/dev/null || true)
    if [[ "$actual" == "$expected" ]]; then
      info "商品关键词索引已就绪（$actual 件）"
      return 0
    fi
    sleep 2
  done
  die "商品关键词索引数量不一致：数据库=$expected Elasticsearch=$actual"
}

print_summary() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  info "本地全栈已启动"
  printf "  %-16s %s\n" "商城前端" "http://127.0.0.1:$WEB_PORT"
  printf "  %-16s %s\n" "管理前端" "http://127.0.0.1:$ADMIN_WEB_PORT/admin/"
  printf "  %-16s %s\n" "Gateway" "http://127.0.0.1:$GATEWAY_PORT"
  printf "  %-16s %s\n" "Agent" "http://127.0.0.1:$AGENT_PORT/health"
  printf "  %-16s %s\n" "Agent Worker 指标" "http://127.0.0.1:$AGENT_WORKER_METRICS_PORT/metrics"
  printf "  %-16s %s\n" "Nacos" "http://127.0.0.1:$NACOS_PORT/nacos/"
  printf "  %-16s %s\n" "RabbitMQ" "http://127.0.0.1:$RABBIT_MANAGEMENT_PORT"
  printf "  %-16s %s\n" "Elasticsearch" "http://127.0.0.1:$ES_PORT"
  printf "  %-16s %s\n" "Sentinel" "http://127.0.0.1:$SENTINEL_PORT"
  printf "  %-16s %s\n" "Seata 控制台" "http://127.0.0.1:$SEATA_CONSOLE_PORT"
  printf "  %-16s %s\n" "商品目录" "$(<"$SIMLECT_VERSION")"
  printf "  %-16s %s\n" "图片目录" "${PROJECT_FOLDER}file/"
  printf "  %-16s %s\n" "本地运行配置" "$RUNTIME_ENV"
  printf "  %-16s %s\n" "日志目录" "$LOGS"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

require_command docker
require_command curl
require_command java
require_command nohup
require_command setsid
docker info >/dev/null 2>&1 || die "Docker Desktop 未就绪或当前用户没有 Docker 权限"

prepare_environment
start_middleware

if $MIDDLEWARE_ONLY; then
  info "仅启动中间件，退出"
  exit 0
fi

resolve_python
ensure_jars_or_build
install_catalog_assets

info "启动 Java 微服务..."
start_java gateway "$BACKEND/AI_Shop-gateway/target/aishop-gateway-1.0.0.jar" "$GATEWAY_PORT"
wait_managed_service gateway "$GATEWAY_PORT" "Gateway" 240
wait_http "http://127.0.0.1:$GATEWAY_PORT/actuator/health" "Gateway 健康检查" 240

# Cold-start services one at a time.  Starting all Spring contexts together
# exhausts WSL swap on development machines that also run unrelated containers.
start_java product "$BACKEND/AI_Shop-product/app/target/aishop-product-1.0.0.jar" "$PRODUCT_PORT"
wait_managed_service product "$PRODUCT_PORT" "Product 服务" 300
wait_http "http://127.0.0.1:$PRODUCT_PORT/actuator/health" "Product 健康检查" 240

start_java stock   "$BACKEND/AI_Shop-stock/app/target/aishop-stock-1.0.0.jar"     "$STOCK_PORT"
wait_managed_service stock "$STOCK_PORT" "Stock 服务" 300
wait_http "http://127.0.0.1:$STOCK_PORT/actuator/health" "Stock 健康检查" 240

# Product and Stock Flyway schemas are prerequisites for this cross-database seed.
seed_product_data
if $CATALOG_CHANGED; then
  # Product loaded its Bloom filter before the new rows existed. Search may be
  # left over from a previous invocation, so stop it before deleting indexes.
  stop_managed_service_for_restart product
  stop_managed_service_for_restart search
  reset_catalog_indexes
  start_java product "$BACKEND/AI_Shop-product/app/target/aishop-product-1.0.0.jar" "$PRODUCT_PORT"
  wait_managed_service product "$PRODUCT_PORT" "Product 服务（目录刷新）" 300
  wait_http "http://127.0.0.1:$PRODUCT_PORT/actuator/health" "Product 健康检查（目录刷新）" 240
fi

for service_spec in \
  "user|$BACKEND/AI_Shop-user/app/target/aishop-user-1.0.0.jar|$USER_PORT" \
  "cart|$BACKEND/AI_Shop-cart/app/target/aishop-cart-1.0.0.jar|$CART_PORT" \
  "order|$BACKEND/AI_Shop-order/app/target/aishop-order-1.0.0.jar|$ORDER_PORT" \
  "pay|$BACKEND/AI_Shop-pay/app/target/aishop-pay-1.0.0.jar|$PAY_PORT" \
  "coupon|$BACKEND/AI_Shop-coupon/app/target/aishop-coupon-1.0.0.jar|$COUPON_PORT" \
  "search|$BACKEND/AI_Shop-search/target/aishop-search-1.0.0.jar|$SEARCH_PORT" \
  "admin|$BACKEND/AI_Shop-admin/target/aishop-admin-1.0.0.jar|$ADMIN_PORT"; do
  IFS='|' read -r service jar port <<<"$service_spec"
  start_java "$service" "$jar" "$port"
  wait_managed_service "$service" "$port" "$service 服务" 300
  wait_http "http://127.0.0.1:$port/actuator/health" "$service 健康检查" 240
done

rebuild_catalog_search_index

info "启动 MCP 与 Python Agent..."
start_mcp
wait_managed_service mcp "$MCP_PORT" "MCP Server"
start_agent_api
wait_managed_service agent "$AGENT_PORT" "Agent API"
wait_http "http://127.0.0.1:$AGENT_PORT/health/live" "Agent 存活检查" 120
start_agent_worker
wait_managed_service agent-worker "$AGENT_WORKER_METRICS_PORT" "Agent Worker"
wait_http "http://127.0.0.1:$AGENT_PORT/health/ready" "Agent 就绪检查" 180

info "启动 Vite 前端..."
start_storefront
wait_managed_service web "$WEB_PORT" "商城前端"
wait_http "http://127.0.0.1:$WEB_PORT/" "商城前端" 120
start_admin_web
wait_managed_service admin-web "$ADMIN_WEB_PORT" "管理前端"
wait_http "http://127.0.0.1:$ADMIN_WEB_PORT/admin/" "管理前端" 120

print_summary
