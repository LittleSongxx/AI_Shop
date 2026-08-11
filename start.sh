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
JAVA_BUILD_STAMP="$ROOT/run/java-build.stamp"
LOCAL_ENV="$ROOT/.env.local"
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
STARTED_SERVICES=()
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
# shellcheck source=deploy/agent-ai-env.sh
source "$DEPLOY/agent-ai-env.sh"

# Optional private, repo-local business settings.  The file is ignored by Git
# and is intentionally separate from run/runtime.env, which this script owns
# and rewrites with generated ports and middleware credentials.
if [[ -r "$LOCAL_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$LOCAL_ENV"
  set +a
fi

record_started_service() {
  local name=$1 existing
  for existing in "${STARTED_SERVICES[@]}"; do
    [[ "$existing" == "$name" ]] && return 0
  done
  STARTED_SERVICES+=("$name")
}

rollback_started_services() {
  local status=$? index name
  trap - EXIT
  if ((status != 0)) && ((${#STARTED_SERVICES[@]} > 0)); then
    warn "启动失败，回滚本次新拉起的 ${#STARTED_SERVICES[@]} 个托管进程；Docker 中间件保持运行"
    set +e
    for ((index=${#STARTED_SERVICES[@]} - 1; index >= 0; index--)); do
      name=${STARTED_SERVICES[$index]}
      stop_managed_service_for_restart "$name" 20
    done
  fi
  exit "$status"
}

trap rollback_started_services EXIT

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

# WSL can reject a bind because the Windows host still owns a forwarded port
# even though the listener is invisible to Linux ss(8).  Probe the same IPv4
# wildcard bind used by the local Java processes when netcat is available.
port_is_bindable() {
  local port=$1 status
  if ! command -v nc >/dev/null 2>&1 || ! command -v timeout >/dev/null 2>&1; then
    return 0
  fi
  if timeout --signal=TERM 0.25s nc -4 -l "$port" </dev/null >/dev/null 2>&1; then
    status=0
  else
    status=$?
  fi
  [[ $status -eq 0 || $status -eq 124 ]]
}

report_port_bind_failure() {
  local port=$1 listeners
  listeners=$(ss -H -ltnp "sport = :$port" 2>/dev/null || true)
  if [[ -n "$listeners" ]]; then
    warn "端口 $port 的 Linux 监听者: $listeners"
  elif ! port_is_bindable "$port"; then
    warn "端口 $port 没有 Linux 监听者但仍无法绑定；可能由 WSL/宿主端口转发占用"
  else
    warn "端口 $port 的冲突已在服务退出前后消失，将按有界策略更换端口重试"
  fi
}

show_service_log() {
  local name=$1 log="$LOGS/$1.log"
  [[ -f "$log" ]] || return 0
  warn "$name 最近的日志："
  tail -n 40 "$log" >&2 || true
}

reset_service_log() {
  : >"$LOGS/$1.log"
}

service_startup_failed() {
  local name=$1 log="$LOGS/$1.log"
  [[ -f "$log" ]] || return 1
  grep -Eq "APPLICATION FAILED TO START|Web server failed to start|Port [0-9]+ is already in use|Address already in use" "$log"
}

service_target_port_bind_failed() {
  local name=$1 port=$2 log="$LOGS/$1.log"
  [[ -f "$log" ]] || return 1
  grep -Eiq \
    "Port[[:space:]]+$port[[:space:]]+(was|is)[[:space:]]+already[[:space:]]+in[[:space:]]+use|bind on address .*[, :]$port.*address already in use" \
    "$log"
}

wait_port_released() {
  local port=$1 timeout=${2:-8} end=$((SECONDS + timeout))
  while [[ $SECONDS -lt $end ]]; do
    if ! port_is_listening "$port"; then
      return 0
    fi
    sleep 1
  done
  ! port_is_listening "$port"
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
  local retry_limit=${AISHOP_START_BIND_RETRIES:-4} retry_count=0
  local end=$((SECONDS + timeout)) identity_end=$((SECONDS + 5))
  echo -n "  等待 $label..."
  while [[ $SECONDS -lt $end ]]; do
    if service_startup_failed "$name"; then
      local target_bind_failed=false retry_allowed=false previous_port=$port
      if service_target_port_bind_failed "$name" "$port"; then
        target_bind_failed=true
        report_port_bind_failure "$port"
      fi
      stop_managed_service_for_restart "$name" 10 || true
      if $target_bind_failed && ((retry_count < retry_limit)); then
        if managed_service_supports_port_reassignment "$name"; then
          reassign_managed_port_after_bind_failure "$name" "$port"
          port=$(managed_service_port "$name")
          retry_allowed=true
        elif wait_port_released "$port" 8; then
          retry_allowed=true
        fi
      fi
      if $retry_allowed; then
        retry_count=$((retry_count + 1))
        echo " 端口竞态"
        if [[ "$port" == "$previous_port" ]]; then
          warn "$label 的目标端口 $port 已重新空闲，进行第 $retry_count/$retry_limit 次有界重试"
        else
          warn "$label 的目标端口 $previous_port 发生占用竞态，改用 $port 进行第 $retry_count/$retry_limit 次有界重试"
        fi
        sleep $((retry_count * 2))
        restart_managed_service_after_bind_failure "$name" "$port"
        if managed_service_supports_port_reassignment "$name"; then
          port=$(managed_service_port "$name")
        fi
        end=$((SECONDS + timeout))
        identity_end=$((SECONDS + 5))
        echo -n "  等待 $label..."
        continue
      fi
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
    port_is_bindable "$port"
    return
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

managed_service_port_binding() {
  case "$1" in
    gateway)   printf '%s|%s\n' GATEWAY_PORT "Gateway" ;;
    user)      printf '%s|%s\n' USER_PORT "User 服务" ;;
    product)   printf '%s|%s\n' PRODUCT_PORT "Product 服务" ;;
    stock)     printf '%s|%s\n' STOCK_PORT "Stock 服务" ;;
    cart)      printf '%s|%s\n' CART_PORT "Cart 服务" ;;
    order)     printf '%s|%s\n' ORDER_PORT "Order 服务" ;;
    pay)       printf '%s|%s\n' PAY_PORT "Pay 服务" ;;
    coupon)    printf '%s|%s\n' COUPON_PORT "Coupon 服务" ;;
    search)    printf '%s|%s\n' SEARCH_PORT "Search 服务" ;;
    admin)     printf '%s|%s\n' ADMIN_PORT "Admin 服务" ;;
    mcp)       printf '%s|%s\n' MCP_PORT "MCP Server" ;;
    agent)     printf '%s|%s\n' AGENT_PORT "Agent API" ;;
    agent-worker) printf '%s|%s\n' AGENT_WORKER_METRICS_PORT "Agent Worker 指标" ;;
    web)       printf '%s|%s\n' WEB_PORT "商城前端" ;;
    admin-web) printf '%s|%s\n' ADMIN_WEB_PORT "管理前端" ;;
    *) return 1 ;;
  esac
}

managed_service_supports_port_reassignment() {
  managed_service_port_binding "$1" >/dev/null
}

managed_service_port() {
  local binding variable
  binding=$(managed_service_port_binding "$1") || return 1
  IFS='|' read -r variable _ <<<"$binding"
  printf '%s\n' "${!variable}"
}

reassign_managed_port_after_bind_failure() {
  local name=$1 previous_port=$2 binding variable label
  local next_port=$((10#$previous_port + 1))
  binding=$(managed_service_port_binding "$name") \
    || die "服务 $name 不支持动态重分配端口"
  IFS='|' read -r variable label <<<"$binding"
  case "$name" in
    gateway|user|product|stock|cart|order|pay|coupon|search|admin|mcp|agent|agent-worker|web|admin-web) ;;
    *) die "服务 $name 不支持动态重分配端口" ;;
  esac
  ((next_port <= 65535)) || die "$label 在端口竞态后没有可用端口"
  printf -v "$variable" '%s' "$next_port"
  export "$variable"
  assign_port "$variable" "$next_port" "$label"
  if [[ "$name" == "gateway" ]]; then
    export JAVA_WEB_URL="http://127.0.0.1:$GATEWAY_PORT"
  elif [[ "$name" == "mcp" ]]; then
    export FASTMCP_PORT="$MCP_PORT"
    export MCP_SERVER_URL="http://127.0.0.1:$MCP_PORT"
  elif [[ "$name" == "agent" ]]; then
    export APP_PORT="$AGENT_PORT"
    export AGENT_BASE_URL="http://127.0.0.1:$AGENT_PORT"
  elif [[ "$name" == "agent-worker" ]]; then
    export WORKER_METRICS_PORT="$AGENT_WORKER_METRICS_PORT"
  fi
  write_runtime_env
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
RUNTIME_CREDENTIAL_VARIABLES=(
  AISHOP_INTERNAL_TOKEN
  MYSQL_ROOT_PASSWORD MYSQL_USER MYSQL_PASSWORD
  ANALYTICS_MYSQL_USER ANALYTICS_MYSQL_PASSWORD
  RABBIT_USER RABBIT_PASSWORD RABBIT_VHOST
  SEATA_CONSOLE_USERNAME SEATA_CONSOLE_PASSWORD SEATA_SECURITY_SECRET_KEY
)
RUNTIME_SETTING_VARIABLES=(
  ES_INDEX_REPLICAS
  MULTI_AGENT_ENABLED DATA_ANALYST_ENABLED
  SHOPPING_DECISION_V2_ENABLED OUTCOME_LEDGER_ENABLED
  AFTER_SALES_POLICY_ENGINE_ENABLED INVENTORY_OPS_ENABLED
  VISUAL_SEARCH_ENABLED VISUAL_INDEX_CONSUMER_ENABLED VISUAL_INDEX_BACKFILL_ON_START
  ANALYTICS_MYSQL_HOST ANALYTICS_MYSQL_PORT ANALYTICS_MYSQL_DATABASE
)
RUNTIME_VARIABLES=(
  "${PORT_VARIABLES[@]}"
  "${RUNTIME_CREDENTIAL_VARIABLES[@]}"
  "${RUNTIME_SETTING_VARIABLES[@]}"
)

# A caller-supplied value wins over the saved local runtime assignment.
declare -A CALLER_VALUES=()
for variable in "${RUNTIME_VARIABLES[@]}" MYSQL_HOST SEATA_IP; do
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

# runtime.env stores the ports that happened to be assigned on the previous
# run. A transient frontend collision must not turn an automatically selected
# fallback into the permanent preference for later clean starts. Explicit
# values from the caller or .env.local still win.
if [[ -z "${CALLER_VALUES[WEB_PORT]+x}" ]]; then
  unset WEB_PORT
fi
if [[ -z "${CALLER_VALUES[ADMIN_WEB_PORT]+x}" ]]; then
  unset ADMIN_WEB_PORT
fi

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

normalize_boolean_setting() {
  local variable=$1 default_value=$2 value=${!1:-$2}
  case "${value,,}" in
    1|true|yes|on) value=true ;;
    0|false|no|off) value=false ;;
    *) die "$variable 必须是 true/false，当前值: $value" ;;
  esac
  printf -v "$variable" '%s' "$value"
  export "$variable"
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
  AISHOP_START_BIND_RETRIES="${AISHOP_START_BIND_RETRIES:-4}"
  [[ "$AISHOP_START_BIND_RETRIES" =~ ^[0-5]$ ]] \
    || die "AISHOP_START_BIND_RETRIES 必须是 0..5 的整数，当前值: $AISHOP_START_BIND_RETRIES"
  export AISHOP_START_BIND_RETRIES
  prepare_ports
  # Java Search and the Python Agent share AI provider settings. The Agent
  # reads dotenv itself, while Spring Boot only sees the process environment.
  load_agent_ai_env "$BACKEND/AI_Shop-agent/.env"

  export MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
  # The bundled local stack uses MySQL root for both schema initialization and
  # Java services. On a fresh setup, accepting MYSQL_PASSWORD as the fallback
  # keeps the container and clients aligned for callers that only set the
  # application-facing variable. MYSQL_ROOT_PASSWORD remains authoritative.
  export MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-${MYSQL_PASSWORD:-root}}"
  export MYSQL_USER="${MYSQL_USER:-root}"
  export MYSQL_PASSWORD="${MYSQL_PASSWORD:-$MYSQL_ROOT_PASSWORD}"
  normalize_boolean_setting MULTI_AGENT_ENABLED true
  normalize_boolean_setting DATA_ANALYST_ENABLED true
  normalize_boolean_setting SHOPPING_DECISION_V2_ENABLED true
  normalize_boolean_setting OUTCOME_LEDGER_ENABLED true
  normalize_boolean_setting AFTER_SALES_POLICY_ENGINE_ENABLED true
  normalize_boolean_setting INVENTORY_OPS_ENABLED true
  normalize_boolean_setting VISUAL_SEARCH_ENABLED true
  normalize_boolean_setting VISUAL_INDEX_CONSUMER_ENABLED true
  normalize_boolean_setting VISUAL_INDEX_BACKFILL_ON_START true
  if [[ -z "${CALLER_VALUES[ANALYTICS_MYSQL_HOST]+x}" ]]; then
    ANALYTICS_MYSQL_HOST="$MYSQL_HOST"
  fi
  if [[ -z "${CALLER_VALUES[ANALYTICS_MYSQL_PORT]+x}" ]]; then
    ANALYTICS_MYSQL_PORT="$MYSQL_PORT"
  fi
  export ANALYTICS_MYSQL_HOST ANALYTICS_MYSQL_PORT
  export ANALYTICS_MYSQL_USER="${ANALYTICS_MYSQL_USER:-analytics_reader}"
  export ANALYTICS_MYSQL_DATABASE="${ANALYTICS_MYSQL_DATABASE:-aishop_admin}"
  if [[ "$DATA_ANALYST_ENABLED" == "true" && -z "${ANALYTICS_MYSQL_PASSWORD:-}" ]]; then
    ANALYTICS_MYSQL_PASSWORD=$(random_token)
  fi
  export ANALYTICS_MYSQL_PASSWORD="${ANALYTICS_MYSQL_PASSWORD:-}"
  export REDIS_HOST="127.0.0.1"
  export RABBIT_HOST="127.0.0.1"
  export RABBIT_USER="${RABBIT_USER:-aishop}"
  export RABBIT_PASSWORD="${RABBIT_PASSWORD:-aishop}"
  export RABBIT_VHOST="${RABBIT_VHOST:-/}"
  export SEATA_CONSOLE_USERNAME="${SEATA_CONSOLE_USERNAME:-seata}"
  export SEATA_CONSOLE_PASSWORD="${SEATA_CONSOLE_PASSWORD:-seata}"
  export SEATA_SECURITY_SECRET_KEY="${SEATA_SECURITY_SECRET_KEY:-SeataSecretKey0c382ef121d778043159209298fd40bf3850a017}"
  export NACOS_ADDR="127.0.0.1:$NACOS_PORT"
  export SENTINEL_DASHBOARD="127.0.0.1:$SENTINEL_PORT"
  export SEATA_SERVER_ADDR="127.0.0.1:$SEATA_PORT"
  export SEATA_IP="${SEATA_IP:-$(detect_host_ip)}"
  export ES_URIS="http://127.0.0.1:$ES_PORT"
  export ES_HOSTS="$ES_URIS"
  export SPRING_ELASTICSEARCH_URIS="$ES_URIS"
  ES_INDEX_REPLICAS="${ES_INDEX_REPLICAS:-0}"
  [[ "$ES_INDEX_REPLICAS" =~ ^[0-9]+$ ]] \
    || die "ES_INDEX_REPLICAS 必须是 0..20 的整数，当前值: $ES_INDEX_REPLICAS"
  ES_INDEX_REPLICAS=$((10#$ES_INDEX_REPLICAS))
  ((ES_INDEX_REPLICAS <= 20)) \
    || die "ES_INDEX_REPLICAS 必须是 0..20 的整数，当前值: $ES_INDEX_REPLICAS"
  export ES_INDEX_REPLICAS
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
  if [[ "$JAVA_OPTS" != *"-Djava.net.preferIPv4Stack="* ]]; then
    # Nacos and all local service URLs are IPv4. Avoid WSL's intermittent
    # dual-stack wildcard collision while preserving an explicit caller choice.
    JAVA_OPTS="$JAVA_OPTS -Djava.net.preferIPv4Stack=true"
    export JAVA_OPTS
  fi

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
    else
      PYTHON=""
    fi
  fi
  [[ -n "$PYTHON" && -x "$PYTHON" ]] \
    || die "未找到 Conda shop 环境；请先创建该环境，或用 AISHOP_PYTHON 显式指定解释器"
  export AISHOP_PYTHON="$PYTHON"
  info "Agent Python: $PYTHON"
}

validate_agent_config() {
  local agent_dir="$BACKEND/AI_Shop-agent"
  if ! (
    cd "$agent_dir"
    "$PYTHON" - <<'PY'
import sys

try:
    import importlib

    from pydantic import ValidationError
    from app.config.settings import Settings
except Exception as exc:
    print(
        f"Agent 配置预检无法加载 Python 依赖：{type(exc).__name__}: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1)

try:
    if not ((3, 11) <= sys.version_info[:2] < (3, 14)):
        raise RuntimeError(
            f"Python {sys.version_info.major}.{sys.version_info.minor} is unsupported; expected 3.11-3.13"
        )
    settings = Settings()
    settings.validate_runtime()
    for module in ("app.main", "app.worker", "app.mcp_server"):
        importlib.import_module(module)
except ValidationError as exc:
    print("Agent 配置校验失败：", file=sys.stderr)
    for item in exc.errors(include_input=False, include_url=False):
        print(f"  - {item.get('msg', '配置值无效')}", file=sys.stderr)
    raise SystemExit(1)
except Exception as exc:
    print(
        f"Agent 配置校验失败：{type(exc).__name__}: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
  ); then
    die "请修正 Agent Python 依赖或运行配置后重试；未启动任何项目服务"
  fi
  info "Agent 配置与 API/Worker/MCP 入口预检通过"
}

JAVA_JARS=(
  "$BACKEND/AI_Shop-gateway/target/aishop-gateway-1.0.0.jar"
  "$BACKEND/AI_Shop-user/app/target/aishop-user-1.0.0.jar"
  "$BACKEND/AI_Shop-product/app/target/aishop-product-1.0.0.jar"
  "$BACKEND/AI_Shop-stock/app/target/aishop-stock-1.0.0.jar"
  "$BACKEND/AI_Shop-cart/app/target/aishop-cart-1.0.0.jar"
  "$BACKEND/AI_Shop-order/app/target/aishop-order-1.0.0.jar"
  "$BACKEND/AI_Shop-pay/app/target/aishop-pay-1.0.0.jar"
  "$BACKEND/AI_Shop-coupon/app/target/aishop-coupon-1.0.0.jar"
  "$BACKEND/AI_Shop-search/target/aishop-search-1.0.0.jar"
  "$BACKEND/AI_Shop-admin/target/aishop-admin-1.0.0.jar"
)

JAVA_MANAGED_SERVICES=(
  admin search coupon pay order cart stock product user gateway
)

java_build_inputs_are_stale() {
  [[ -f "$JAVA_BUILD_STAMP" ]] || return 0
  find "$BACKEND" \
    \( -path '*/target' -o -path "$BACKEND/AI_Shop-agent" \) -prune -o \
    -type f \( -name pom.xml -o -path '*/src/main/*' \) \
    -newer "$JAVA_BUILD_STAMP" -print -quit | grep -q .
}

ensure_jars_or_build() {
  local jar service missing=false stale=false
  for jar in "${JAVA_JARS[@]}"; do
    if [[ ! -f "$jar" ]]; then
      missing=true
    fi
  done
  if java_build_inputs_are_stale; then
    stale=true
  fi

  if $BUILD || $missing || $stale; then
    require_command mvn
    if $stale && ! $BUILD && ! $missing; then
      info "检测到 Java 源码或资源比现有 JAR 新，自动重新构建..."
    else
      info "Maven 构建中（-DskipTests）..."
    fi
    # Spring Boot reads nested JAR entries lazily. Replacing an executable JAR
    # underneath a live JVM can therefore surface as NoClassDefFoundError long
    # after the build itself succeeded.
    for service in "${JAVA_MANAGED_SERVICES[@]}"; do
      if is_running "$service"; then
        warn "Java JAR 即将重建，先停止正在运行的 $service"
        stop_managed_service_for_restart "$service"
      fi
    done
    (
      cd "$BACKEND"
      MAVEN_OPTS="${MAVEN_OPTS:--Xmx1024m -XX:+UseSerialGC}" \
        mvn -q clean package -DskipTests
    )
    touch "$JAVA_BUILD_STAMP"
    info "Maven 构建完成"
  else
    info "Java JAR 已是最新，跳过 Maven 构建"
  fi
}

configure_elasticsearch_indexes() {
  local template_body settings_body
  template_body=$(printf \
    '{"index_patterns":["aishop-*","aishop_*"],"priority":200,"template":{"settings":{"number_of_replicas":%d}}}' \
    "$ES_INDEX_REPLICAS")
  settings_body=$(printf \
    '{"index":{"number_of_replicas":%d}}' \
    "$ES_INDEX_REPLICAS")

  curl --noproxy '*' -fsS --max-time 15 \
    -X PUT -H 'Content-Type: application/json' \
    --data "$template_body" \
    "$ES_URIS/_index_template/aishop-runtime-defaults" >/dev/null \
    || die "写入 Elasticsearch 索引模板失败"

  # Update only project-owned indexes. allow_no_indices keeps a clean first
  # start successful before Spring creates either index.
  curl --noproxy '*' -fsS --max-time 15 \
    -X PUT -H 'Content-Type: application/json' \
    --data "$settings_body" \
    "$ES_URIS/aishop-*,aishop_*/_settings?allow_no_indices=true&ignore_unavailable=true&expand_wildcards=open" \
    >/dev/null \
    || die "更新 Elasticsearch 项目索引副本数失败"
  info "Elasticsearch 项目索引副本数已设为 $ES_INDEX_REPLICAS"
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
  configure_elasticsearch_indexes
  wait_port "$SENTINEL_PORT" "Sentinel" 120
  wait_port "$SEATA_PORT" "Seata TC" 180
  wait_http "http://127.0.0.1:$NACOS_PORT/nacos/" "Nacos HTTP" 60
}

ensure_port_free() {
  local port=$1 label=$2
  if port_is_listening "$port" || ! port_is_bindable "$port"; then
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
  if port_is_listening "$port" || ! port_is_bindable "$port"; then
    reassign_managed_port_after_bind_failure "$name" "$port"
    port=$(managed_service_port "$name")
    warn "$name 的目标端口在分配后被占用，启动端口调整为 $port"
  fi
  [[ -f "$jar" ]] || die "JAR 不存在: $jar；请使用 ./start.sh --build"
  local -a java_opts
  read -r -a java_opts <<<"$JAVA_OPTS"
  reset_service_log "$name"
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
  record_started_service "$name"
  info "拉起 $name  port=$port  pid=$pid  log: run/logs/$name.log"
}

start_mcp() {
  if is_running mcp; then
    load_pid_record mcp
    warn "mcp 已运行 (pid=$SERVICE_PID)，跳过拉起"
    return 0
  fi
  ensure_memory_headroom "MCP"
  if port_is_listening "$MCP_PORT" || ! port_is_bindable "$MCP_PORT"; then
    reassign_managed_port_after_bind_failure mcp "$MCP_PORT"
    warn "MCP 的目标端口在分配后被占用，启动端口调整为 $MCP_PORT"
  fi
  reset_service_log mcp
  (
    cd "$BACKEND/AI_Shop-agent"
    export AISHOP_MANAGED_SERVICE=mcp
    exec setsid nohup "$PYTHON" -m app.mcp_server
  ) 9>&- </dev/null >"$LOGS/mcp.log" 2>&1 &
  local pid=$!
  write_pid_record mcp "$pid"
  record_started_service mcp
  info "拉起 mcp  port=$MCP_PORT  pid=$pid  log: run/logs/mcp.log"
}

start_agent_api() {
  if is_running agent; then
    load_pid_record agent
    warn "agent 已运行 (pid=$SERVICE_PID)，跳过拉起"
    return 0
  fi
  ensure_memory_headroom "Agent API"
  if port_is_listening "$AGENT_PORT" || ! port_is_bindable "$AGENT_PORT"; then
    reassign_managed_port_after_bind_failure agent "$AGENT_PORT"
    warn "Agent API 的目标端口在分配后被占用，启动端口调整为 $AGENT_PORT"
  fi
  reset_service_log agent
  (
    cd "$BACKEND/AI_Shop-agent"
    export AISHOP_MANAGED_SERVICE=agent
    exec setsid nohup "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "$AGENT_PORT"
  ) 9>&- </dev/null >"$LOGS/agent.log" 2>&1 &
  local pid=$!
  write_pid_record agent "$pid"
  record_started_service agent
  info "拉起 agent  port=$AGENT_PORT  pid=$pid  log: run/logs/agent.log"
}

start_agent_worker() {
  if is_running agent-worker; then
    load_pid_record agent-worker
    warn "agent-worker 已运行 (pid=$SERVICE_PID)，跳过拉起"
    return 0
  fi
  ensure_memory_headroom "Agent Worker"
  if port_is_listening "$AGENT_WORKER_METRICS_PORT" \
    || ! port_is_bindable "$AGENT_WORKER_METRICS_PORT"; then
    reassign_managed_port_after_bind_failure agent-worker "$AGENT_WORKER_METRICS_PORT"
    warn "Agent Worker 指标端口在分配后被占用，启动端口调整为 $AGENT_WORKER_METRICS_PORT"
  fi
  reset_service_log agent-worker
  (
    cd "$BACKEND/AI_Shop-agent"
    export AISHOP_MANAGED_SERVICE=agent-worker
    exec setsid nohup "$PYTHON" -m app.worker
  ) 9>&- </dev/null >"$LOGS/agent-worker.log" 2>&1 &
  local pid=$!
  write_pid_record agent-worker "$pid"
  record_started_service agent-worker
  info "拉起 agent-worker  metrics=$AGENT_WORKER_METRICS_PORT  pid=$pid  log: run/logs/agent-worker.log"
}

migrate_agent_schema() {
  info "在 Admin 创建治理视图前迁移 Agent 数据库..."
  (
    cd "$BACKEND/AI_Shop-agent"
    "$PYTHON" scripts/migrate.py
  ) || die "Agent 数据库迁移失败"
  local schema_count
  schema_count=$(mysql_query aishop_agent -e \
    "SELECT COUNT(DISTINCT schema_key) FROM agent_category_need_schema WHERE status='PUBLISHED';" \
    | tr -d '[:space:]')
  [[ "$schema_count" =~ ^[0-9]+$ && "$schema_count" -ge 7 ]] \
    || die "类目需求 schema 种子不完整：当前仅有 ${schema_count:-0} 个已发布 schema"
  info "已发布 $schema_count 个类目需求 schema"
}

bootstrap_demo_data() {
  local enabled="${AISHOP_DEMO_DATA_ENABLED:-false}"
  case "${enabled,,}" in
    1|true|yes|on) ;;
    *) return 0 ;;
  esac

  local script="$ROOT/scripts/bootstrap_demo.py"
  [[ -f "$script" ]] || die "演示初始化脚本不存在: $script"
  info "初始化 Smarlect 本地演示数据（串行执行）..."
  "$PYTHON" "$script" --wait-seconds "${AISHOP_DEMO_WAIT_SECONDS:-180}"
  info "Smarlect 本地演示数据已就绪"
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
  if port_is_listening "$WEB_PORT" || ! port_is_bindable "$WEB_PORT"; then
    reassign_managed_port_after_bind_failure web "$WEB_PORT"
    warn "商城前端的目标端口在分配后被占用，启动端口调整为 $WEB_PORT"
  fi
  reset_service_log web
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
  record_started_service web
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
  if port_is_listening "$ADMIN_WEB_PORT" || ! port_is_bindable "$ADMIN_WEB_PORT"; then
    reassign_managed_port_after_bind_failure admin-web "$ADMIN_WEB_PORT"
    warn "管理前端的目标端口在分配后被占用，启动端口调整为 $ADMIN_WEB_PORT"
  fi
  reset_service_log admin-web
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
  record_started_service admin-web
  info "拉起 admin-web  port=$ADMIN_WEB_PORT  pid=$pid  log: run/logs/admin-web.log"
}

restart_managed_service_after_bind_failure() {
  local name=$1 port=$2 jar
  case "$name" in
    gateway|user|product|stock|cart|order|pay|coupon|search|admin)
      jar=$(service_marker "$name")
      start_java "$name" "$jar" "$port"
      ;;
    mcp) start_mcp ;;
    agent) start_agent_api ;;
    agent-worker) start_agent_worker ;;
    web) start_storefront ;;
    admin-web) start_admin_web ;;
    *) die "未知托管服务 $name，无法执行端口竞态重试" ;;
  esac
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

refresh_dynamic_runtime_services() {
  local name
  # Python imports application modules once and Vite captures proxy targets at
  # process start. A healthy old process can therefore serve stale Agent code
  # or proxy to ports from a previous runtime.env. Reconcile these lightweight
  # services on every full start after Java ports and indexes are final.
  for name in admin-web web agent-worker agent mcp; do
    if is_running "$name"; then
      warn "刷新动态运行层，重启 $name 以加载当前代码与运行配置"
      stop_managed_service_for_restart "$name" 30
    fi
  done

  # Frontend ports are initially reserved before stale Vite processes are
  # refreshed. Re-run only these two assignments after the listeners have
  # actually stopped so a normal restart can reclaim 6001/6002 instead of
  # persisting a transient fallback such as 6002/6003.
  local previous_web_port=$WEB_PORT previous_admin_web_port=$ADMIN_WEB_PORT
  unset 'RESERVED_PORTS[$previous_web_port]'
  unset 'RESERVED_PORTS[$previous_admin_web_port]'
  if [[ -n "${CALLER_VALUES[WEB_PORT]+x}" ]]; then
    WEB_PORT=${CALLER_VALUES[WEB_PORT]}
    export WEB_PORT
  else
    unset WEB_PORT
  fi
  if [[ -n "${CALLER_VALUES[ADMIN_WEB_PORT]+x}" ]]; then
    ADMIN_WEB_PORT=${CALLER_VALUES[ADMIN_WEB_PORT]}
    export ADMIN_WEB_PORT
  else
    unset ADMIN_WEB_PORT
  fi
  assign_port WEB_PORT 6001 "商城前端"
  assign_port ADMIN_WEB_PORT 6002 "管理前端"
  if [[ "$WEB_PORT" != "$previous_web_port" || "$ADMIN_WEB_PORT" != "$previous_admin_web_port" ]]; then
    info "动态前端端口已重新协调为 $WEB_PORT/$ADMIN_WEB_PORT"
  fi
  write_runtime_env
}

provision_analytics_reader() {
  [[ "$DATA_ANALYST_ENABLED" == "true" ]] || return 0
  if [[ "$ANALYTICS_MYSQL_HOST" != "127.0.0.1" && "$ANALYTICS_MYSQL_HOST" != "localhost" ]]; then
    warn "DataAnalyst 使用外部 MySQL $ANALYTICS_MYSQL_HOST，跳过本地 analytics_reader 配置"
    return 0
  fi
  if [[ "$ANALYTICS_MYSQL_PORT" != "$MYSQL_PORT" ]]; then
    warn "DataAnalyst 使用独立 MySQL 端口 $ANALYTICS_MYSQL_PORT，跳过本地 analytics_reader 配置"
    return 0
  fi

  info "配置 DataAnalyst 只读账号与十个治理视图权限..."
  MYSQL_CONTAINER=aishop-mysql \
    MYSQL_ADMIN_USER=root \
    MYSQL_ADMIN_PASSWORD="$MYSQL_ROOT_PASSWORD" \
    MYSQL_HOST=127.0.0.1 \
    MYSQL_PORT="$MYSQL_PORT" \
    ANALYTICS_MYSQL_USER="$ANALYTICS_MYSQL_USER" \
    ANALYTICS_MYSQL_PASSWORD="$ANALYTICS_MYSQL_PASSWORD" \
    "$DEPLOY/provision-analytics-reader.sh"
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

check_visual_index_runtime() {
  [[ "$VISUAL_SEARCH_ENABLED" == "true" ]] || return 0
  info "检查视觉商品索引与独立消费队列..."
  if ! (
    cd "$BACKEND/AI_Shop-agent"
    "$PYTHON" scripts/check_visual_index.py
  ); then
    warn "视觉商品搜索处于降级状态；商城和其他 Agent 能力将继续启动"
  fi
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
  printf "  %-16s %s\n" "Multi-Agent" "$MULTI_AGENT_ENABLED"
  printf "  %-16s %s\n" "DataAnalyst" "$DATA_ANALYST_ENABLED"
  printf "  %-16s %s\n" "导购决策 v2" "$SHOPPING_DECISION_V2_ENABLED"
  printf "  %-16s %s\n" "结果账本" "$OUTCOME_LEDGER_ENABLED"
  printf "  %-16s %s\n" "售后规则引擎" "$AFTER_SALES_POLICY_ENGINE_ENABLED"
  printf "  %-16s %s\n" "InventoryOps" "$INVENTORY_OPS_ENABLED"
  printf "  %-16s %s\n" "视觉商品搜索" "$VISUAL_SEARCH_ENABLED"
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
if ! $MIDDLEWARE_ONLY; then
  require_command java
  require_command node
  require_command nohup
  require_command setsid
fi
docker info >/dev/null 2>&1 || die "Docker Desktop 未就绪或当前用户没有 Docker 权限"

prepare_environment

# Validate the Python configuration before allocating memory to Docker and ten
# Spring Boot processes. --middleware-only deliberately does not require the
# conda environment or AI credentials.
if ! $MIDDLEWARE_ONLY; then
  resolve_python
  validate_agent_config
  # Build before starting memory-heavy middleware on a clean machine. Existing
  # healthy containers remain untouched, but a stale JAR can no longer run by
  # accident after source or application.yml changes.
  ensure_jars_or_build
fi

start_middleware

if $MIDDLEWARE_ONLY; then
  info "仅启动中间件，退出"
  exit 0
fi

install_catalog_assets

# Admin owns cross-database analytics views that reference Agent decision
# artifacts. Create the Agent schema before Admin Flyway validates those views.
migrate_agent_schema

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
  port=$(managed_service_port "$service")
  wait_managed_service "$service" "$port" "$service 服务" 300
  port=$(managed_service_port "$service")
  wait_http "http://127.0.0.1:$port/actuator/health" "$service 健康检查" 240
done

provision_analytics_reader

rebuild_catalog_search_index

refresh_dynamic_runtime_services

info "启动 MCP 与 Python Agent..."
start_mcp
wait_managed_service mcp "$MCP_PORT" "MCP Server"
start_agent_api
wait_managed_service agent "$AGENT_PORT" "Agent API"
wait_http "http://127.0.0.1:$AGENT_PORT/health/live" "Agent 存活检查" 120
start_agent_worker
wait_managed_service agent-worker "$AGENT_WORKER_METRICS_PORT" "Agent Worker"
wait_http "http://127.0.0.1:$AGENT_PORT/health/ready" "Agent 就绪检查" 180
check_visual_index_runtime

bootstrap_demo_data

info "启动 Vite 前端..."
start_storefront
wait_managed_service web "$WEB_PORT" "商城前端"
wait_http "http://127.0.0.1:$WEB_PORT/" "商城前端" 120
start_admin_web
wait_managed_service admin-web "$ADMIN_WEB_PORT" "管理前端"
wait_http "http://127.0.0.1:$ADMIN_WEB_PORT/admin/" "管理前端" 120

print_summary
