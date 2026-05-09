#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"

INS_ID="${CUBEMASTER_METRIC_INS_ID:-local-ins-1}"
REDIS_HOST="${CUBEMASTER_REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${CUBEMASTER_REDIS_PORT:-${CUBE_SANDBOX_REDIS_PORT:-6379}}"
REDIS_DB="${CUBEMASTER_REDIS_DB:-0}"
REDIS_PASSWORD="${CUBEMASTER_REDIS_PASSWORD:-${CUBE_SANDBOX_REDIS_PASSWORD:-ceuhvu123}}"
REDIS_CONTAINER="${CUBEMASTER_REDIS_CONTAINER:-${CUBE_SANDBOX_REDIS_CONTAINER:-cube-sandbox-redis}}"
INTERVAL="${CUBEMASTER_METRIC_INTERVAL:-1}"
LOOP_MODE="${1:-}"

redis_exec() {
  if command -v redis-cli >/dev/null 2>&1; then
    if [[ -n "${REDIS_PASSWORD}" ]]; then
      redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" -n "${REDIS_DB}" -a "${REDIS_PASSWORD}" "$@"
    else
      redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" -n "${REDIS_DB}" "$@"
    fi
    return
  fi

  if container_exists "${REDIS_CONTAINER}"; then
    if [[ -n "${REDIS_PASSWORD}" ]]; then
      docker exec "${REDIS_CONTAINER}" redis-cli -n "${REDIS_DB}" -a "${REDIS_PASSWORD}" "$@"
    else
      docker exec "${REDIS_CONTAINER}" redis-cli -n "${REDIS_DB}" "$@"
    fi
    return
  fi

  die "redis-cli not found and container ${REDIS_CONTAINER} is not running"
}

seed_once() {
  local now
  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  redis_exec HSET "${INS_ID}" \
    ins_id "${INS_ID}" \
    update_at "${now}" \
    quota_cpu_usage "250" \
    quota_mem_mb_usage "512" \
    cpu_util "0.05" \
    cpu_load_usage "100" \
    mem_load_mb_usage "256" \
    mvm_num "0" \
    realtime_create_num "0" >/dev/null
  log "seeded cubemaster metrics for ${INS_ID}"
}

if [[ "${LOOP_MODE}" == "--loop" ]]; then
  while true; do
    seed_once
    sleep "${INTERVAL}"
  done
else
  seed_once
fi
