#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.fault-drill.yml"
STARTED_AT=$(date '+%Y-%m-%dT%H:%M:%S%z')
EVIDENCE_STAMP=$(date '+%Y%m%d_%H%M%S_%z')
EVIDENCE_DIR="${REPO_ROOT}/run/evidence/${EVIDENCE_STAMP}"
BASELINE_COMMIT="f639599e335b97f6156cc41923d53948bcbf6549"
RESULT="FAIL"
COMPOSE=(docker compose -f "${COMPOSE_FILE}")

mkdir -p "${EVIDENCE_DIR}"

record_command() {
  printf '%s\n' "$*" >>"${EVIDENCE_DIR}/commands.log"
}

compose() {
  record_command "docker compose -f ${COMPOSE_FILE} $*"
  "${COMPOSE[@]}" "$@"
}

wait_for_state() {
  local message_id=$1
  local expression=$2
  local destination=$3
  local timeout_seconds=$4
  local attempt
  for ((attempt = 1; attempt <= timeout_seconds; attempt++)); do
    if curl --fail --silent --show-error \
      "http://127.0.0.1:17050/tasks/${message_id}" \
      >"${destination}.tmp" 2>/dev/null; then
      mv "${destination}.tmp" "${destination}"
      if jq -e "${expression}" "${destination}" >/dev/null; then
        return 0
      fi
    fi
    sleep 1
  done
  printf 'Timed out waiting for message %s: %s\n' "${message_id}" "${expression}" >&2
  return 1
}

post_task() {
  local destination=$1
  local body=$2
  record_command "curl POST http://127.0.0.1:17050/tasks body=${body}"
  curl --fail --silent --show-error \
    -H 'Content-Type: application/json' \
    -d "${body}" \
    http://127.0.0.1:17050/tasks >"${destination}"
}

assert_json() {
  local file=$1
  local expression=$2
  local label=$3
  if ! jq -e "${expression}" "${file}" >/dev/null; then
    printf 'Assertion failed: %s (%s)\n' "${label}" "${expression}" >&2
    jq . "${file}" >&2 || true
    return 1
  fi
  jq -n --arg name "${label}" --arg file "${file}" \
    '{name:$name,status:"PASS",evidence:$file}' \
    >>"${EVIDENCE_DIR}/assertions.jsonl"
}

collect_evidence() {
  set +e
  {
    printf 'started_at=%s\n' "${STARTED_AT}"
    printf 'finished_at=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')"
    printf 'timezone=%s\n' "$(date '+%Z %z')"
    printf 'baseline_commit=%s\n' "${BASELINE_COMMIT}"
    printf 'current_head=%s\n' "$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null)"
    printf 'compose_project=aishop-fault-drill\n'
    printf 'result=%s\n' "${RESULT}"
  } >"${EVIDENCE_DIR}/metadata.txt"
  "${COMPOSE[@]}" --profile workers ps --all >"${EVIDENCE_DIR}/compose-ps.txt" 2>&1
  "${COMPOSE[@]}" config >"${EVIDENCE_DIR}/compose-config.yml" 2>&1
  "${COMPOSE[@]}" --profile workers logs --no-color --timestamps \
    >"${EVIDENCE_DIR}/containers.log" 2>&1
  "${COMPOSE[@]}" exec -T mysql sh -lc \
    "MYSQL_PWD=fault-root mysql -uroot aishop_agent_fault_drill -e \"SELECT message_id,status,retry_count,lease_owner,lease_until,deadline_at,completed_at,error_message FROM agent_task ORDER BY message_id; SELECT attempt_id,message_id,worker_id,mode,outcome,started_at,completed_at,error_message FROM fault_drill_attempt ORDER BY attempt_id; SELECT action_key,message_id,worker_id,created_at FROM fault_drill_effect ORDER BY message_id;\"" \
    >"${EVIDENCE_DIR}/mysql-state.txt" 2>&1
  "${COMPOSE[@]}" exec -T rabbitmq rabbitmqctl -p /fault-drill list_queues \
    name messages_ready messages_unacknowledged consumers \
    >"${EVIDENCE_DIR}/rabbitmq-state.txt" 2>&1
  git -C "${REPO_ROOT}" diff --stat >"${EVIDENCE_DIR}/working-tree-diff-stat.txt" 2>&1
  jq -s '.' "${EVIDENCE_DIR}/assertions.jsonl" \
    >"${EVIDENCE_DIR}/assertions.json" 2>/dev/null || true
  printf '%s\n' "${RESULT}" >"${EVIDENCE_DIR}/RESULT"
}

cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ ${exit_code} -eq 0 ]]; then
    RESULT="PASS"
  fi
  collect_evidence
  set +e
  "${COMPOSE[@]}" --profile workers down --volumes --remove-orphans \
    >"${EVIDENCE_DIR}/cleanup.txt" 2>&1
  docker ps -a --filter 'name=aishop-fault-drill' --format '{{.Names}}' \
    >>"${EVIDENCE_DIR}/cleanup.txt" 2>&1
  docker volume ls --filter 'name=aishop-fault-drill' --format '{{.Name}}' \
    >>"${EVIDENCE_DIR}/cleanup.txt" 2>&1
  printf 'Evidence: %s\n' "${EVIDENCE_DIR}"
  printf 'Result: %s\n' "${RESULT}"
  exit "${exit_code}"
}
trap cleanup EXIT

cd "${REPO_ROOT}"
record_command "$0"
compose --profile workers down --volumes --remove-orphans
compose build llm-stub
compose up -d --wait mysql redis checkpoint-redis rabbitmq llm-stub agent-api

record_command "curl POST http://127.0.0.1:17080/v1/chat/completions"
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -d '{"model":"fault-drill-model","messages":[{"role":"user","content":"ping"}]}' \
  http://127.0.0.1:17080/v1/chat/completions \
  >"${EVIDENCE_DIR}/llm-stub-response.json"
assert_json "${EVIDENCE_DIR}/llm-stub-response.json" \
  '.choices[0].message.content == "fault-drill deterministic response"' \
  'deterministic local LLM stub response'

# Scenario 1: kill the sole consumer after it owns the lease, then start a new worker.
compose --profile workers up -d --wait worker-a
post_task "${EVIDENCE_DIR}/takeover-create.json" \
  '{"messageId":610001,"userId":"fault-user-1","mode":"takeover","actionKey":"takeover-effect","firstAttemptSleepSeconds":90}'
wait_for_state 610001 \
  '.task.status == "PROCESSING" and .attemptCount == 1' \
  "${EVIDENCE_DIR}/takeover-before-kill.json" 20
compose kill --signal SIGKILL worker-a
compose --profile workers up -d --wait worker-b
wait_for_state 610001 \
  '.task.status == "COMPLETED" and .attemptCount == 2 and .effectCount == 1' \
  "${EVIDENCE_DIR}/takeover-final.json" 70
assert_json "${EVIDENCE_DIR}/takeover-final.json" \
  '.attempts[0].worker_id != .attempts[1].worker_id and .effectCount == 1' \
  'worker kill is recovered by a different lease owner with one effect'

# Scenario 2: the HTTP dispatcher records PENDING_RECOVERY while RabbitMQ is down.
compose stop -t 5 worker-b
compose stop -t 5 rabbitmq
post_task "${EVIDENCE_DIR}/publish-failure.json" \
  '{"messageId":610002,"userId":"fault-user-2","mode":"normal","actionKey":"publish-recovery-effect"}'
assert_json "${EVIDENCE_DIR}/publish-failure.json" \
  '.deliveryState == "PENDING_RECOVERY" and .state.task.status == "DISPATCHING"' \
  'RabbitMQ publish failure remains durable as PENDING_RECOVERY'
compose up -d --wait rabbitmq
compose --profile workers up -d --wait worker-b
wait_for_state 610002 \
  '.task.status == "COMPLETED" and .effectCount == 1' \
  "${EVIDENCE_DIR}/publish-recovery-final.json" 35
assert_json "${EVIDENCE_DIR}/publish-recovery-final.json" \
  '.task.status == "COMPLETED" and .attemptCount == 1 and .effectCount == 1' \
  'deferred RabbitMQ task recovers once without duplicate execution'

# Scenario 3: two confirmed RabbitMQ deliveries still share one fenced task row.
post_task "${EVIDENCE_DIR}/duplicate-create.json" \
  '{"messageId":610003,"userId":"fault-user-3","mode":"normal","actionKey":"duplicate-effect","duplicateDeliveries":2}'
wait_for_state 610003 \
  '.task.status == "COMPLETED" and .effectCount == 1' \
  "${EVIDENCE_DIR}/duplicate-final.json" 25
assert_json "${EVIDENCE_DIR}/duplicate-final.json" \
  '.attemptCount == 1 and .effectCount == 1 and (.attempts | length) == 1' \
  'duplicate delivery produces one terminal execution and one side effect'

# Scenario 4: only the checkpoint Redis is removed; the task/user leases stay healthy.
compose stop -t 3 checkpoint-redis
post_task "${EVIDENCE_DIR}/checkpoint-create.json" \
  '{"messageId":610004,"userId":"fault-user-4","mode":"checkpoint_failure","actionKey":"checkpoint-effect"}'
wait_for_state 610004 \
  '.task.status == "DEAD" and .attemptCount == 2 and .effectCount == 0' \
  "${EVIDENCE_DIR}/checkpoint-final.json" 35
assert_json "${EVIDENCE_DIR}/checkpoint-final.json" \
  '.task.status != "COMPLETED" and .effectCount == 0 and ([.attempts[].outcome] | all(. == "CHECKPOINT_ERROR"))' \
  'checkpoint persistence failure never reports task success'

RESULT="PASS"
