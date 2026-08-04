#!/usr/bin/env bash
# Shared PID-file handling for start.sh and stop.sh.
# Callers provide ROOT, PIDS, BACKEND and a warn() function before sourcing this file.

service_marker() {
  case "$1" in
    gateway)      printf '%s\n' "$BACKEND/AI_Shop-gateway/target/aishop-gateway-1.0.0.jar" ;;
    user)         printf '%s\n' "$BACKEND/AI_Shop-user/app/target/aishop-user-1.0.0.jar" ;;
    product)      printf '%s\n' "$BACKEND/AI_Shop-product/app/target/aishop-product-1.0.0.jar" ;;
    stock)        printf '%s\n' "$BACKEND/AI_Shop-stock/app/target/aishop-stock-1.0.0.jar" ;;
    cart)         printf '%s\n' "$BACKEND/AI_Shop-cart/app/target/aishop-cart-1.0.0.jar" ;;
    order)        printf '%s\n' "$BACKEND/AI_Shop-order/app/target/aishop-order-1.0.0.jar" ;;
    pay)          printf '%s\n' "$BACKEND/AI_Shop-pay/app/target/aishop-pay-1.0.0.jar" ;;
    coupon)       printf '%s\n' "$BACKEND/AI_Shop-coupon/app/target/aishop-coupon-1.0.0.jar" ;;
    search)       printf '%s\n' "$BACKEND/AI_Shop-search/target/aishop-search-1.0.0.jar" ;;
    admin)        printf '%s\n' "$BACKEND/AI_Shop-admin/target/aishop-admin-1.0.0.jar" ;;
    mcp)          printf '%s\n' "-m app.mcp_server" ;;
    agent)        printf '%s\n' "app.main:app" ;;
    agent-worker) printf '%s\n' "-m app.worker" ;;
    web)          printf '%s\n' "$ROOT/AI_Shop-front/AI_Shop-web/node_modules/vite/bin/vite.js" ;;
    admin-web)    printf '%s\n' "$ROOT/AI_Shop-front/AI_Shop-admin/node_modules/vite/bin/vite.js" ;;
    *) return 1 ;;
  esac
}

process_start_time() {
  local pid=$1 stat rest
  [[ -r "/proc/$pid/stat" ]] || return 1
  IFS= read -r stat <"/proc/$pid/stat" || return 1
  # Remove pid and the parenthesized command. The original field 22 is then field 20.
  rest=${stat##*) }
  set -- $rest
  [[ $# -ge 20 ]] || return 1
  printf '%s\n' "${20}"
}

process_command() {
  local pid=$1
  if [[ -r "/proc/$pid/cmdline" ]]; then
    tr '\0' ' ' <"/proc/$pid/cmdline"
    return
  fi
  ps -p "$pid" -o args= 2>/dev/null
}

load_pid_record() {
  local name=$1 pidfile="$PIDS/$1.pid"
  SERVICE_PID=""
  SERVICE_STARTED_AT=""
  [[ -r "$pidfile" ]] || return 1
  read -r SERVICE_PID SERVICE_STARTED_AT _ <"$pidfile" || true
  [[ "$SERVICE_PID" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ -z "$SERVICE_STARTED_AT" || "$SERVICE_STARTED_AT" =~ ^[0-9]+$ ]] || return 1
}

write_pid_record() {
  local name=$1 pid=$2 pidfile="$PIDS/$1.pid" started_at tmp
  started_at=$(process_start_time "$pid" 2>/dev/null || true)
  tmp="$pidfile.tmp.$$"
  if [[ -n "$started_at" ]]; then
    printf '%s %s\n' "$pid" "$started_at" >"$tmp"
  else
    printf '%s\n' "$pid" >"$tmp"
  fi
  mv -f "$tmp" "$pidfile"
}

clear_pid_record() {
  rm -f "$PIDS/$1.pid"
}

pid_record_is_current() {
  local name=$1 actual_start
  load_pid_record "$name" || return 1
  kill -0 "$SERVICE_PID" 2>/dev/null || return 1
  if [[ -n "$SERVICE_STARTED_AT" ]]; then
    actual_start=$(process_start_time "$SERVICE_PID" 2>/dev/null || true)
    [[ -n "$actual_start" && "$actual_start" == "$SERVICE_STARTED_AT" ]] || return 1
  fi
}

pid_command_matches_service() {
  local name=$1 marker command
  load_pid_record "$name" || return 1
  marker=$(service_marker "$name") || return 1
  command=$(process_command "$SERVICE_PID" 2>/dev/null || true)
  [[ -n "$command" && "$command" == *"$marker"* ]]
}

is_running() {
  local name=$1 pidfile="$PIDS/$1.pid" started_at
  [[ -e "$pidfile" ]] || return 1
  if ! pid_record_is_current "$name" || ! pid_command_matches_service "$name"; then
    warn "$name: PID 文件无效、进程已退出或 PID 已被复用，清理旧记录"
    clear_pid_record "$name"
    return 1
  fi
  # Upgrade legacy one-field PID files so subsequent checks are protected from PID reuse.
  if [[ -z "$SERVICE_STARTED_AT" ]]; then
    started_at=$(process_start_time "$SERVICE_PID" 2>/dev/null || true)
    [[ -z "$started_at" ]] || write_pid_record "$name" "$SERVICE_PID"
  fi
}

port_open() {
  local host=$1 port=$2
  (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null
}

port_owned_by_pid() {
  local port=$1 pid=$2 owner listeners
  if command -v lsof >/dev/null 2>&1; then
    while IFS= read -r owner; do
      [[ "$owner" == "$pid" ]] && return 0
    done < <(lsof -nP -a -p "$pid" -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
    return 1
  fi
  if command -v ss >/dev/null 2>&1; then
    listeners=$(ss -H -ltnp "sport = :$port" 2>/dev/null || true)
    [[ "$listeners" == *"pid=$pid,"* ]]
    return
  fi
  # A reachable port alone cannot prove that it belongs to the recorded process.
  return 1
}
