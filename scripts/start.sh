#!/usr/bin/env bash
# 启动 AgentPlatform 后端
# 用法:
#   bash scripts/start.sh
#   bash scripts/start.sh --foreground
#   BACKEND_PORT=52716 bash scripts/start.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

BACKEND_PORT="$(ap_backend_port)"
LOG_DIR="$(ap_log_dir)"

FOREGROUND=0

for arg in "$@"; do
  case "${arg}" in
    --foreground) FOREGROUND=1 ;;
    -h|--help)
      cat <<EOF
用法: bash scripts/start.sh [选项]

选项:
  --foreground      前台运行（不加 nohup，便于调试）
  -h, --help        显示此帮助

环境变量:
  BACKEND_PORT      后端端口（默认 ${AP_DEFAULT_BACKEND_PORT}）
  AP_BIND_HOST      监听地址（默认 0.0.0.0）
EOF
      exit 0
      ;;
    *)
      echo "[start] 未知参数: ${arg}（可用 --help 查看）" >&2
      exit 1
      ;;
  esac
done

export AP_BIND_HOST="${AP_BIND_HOST:-0.0.0.0}"

mkdir -p "${LOG_DIR}"

echo "[start] 停止旧进程..."
ap_stop_backend
for _ in $(seq 1 20); do
  if [ -z "$(ap_pids_listening_on_port "${BACKEND_PORT}")" ]; then
    break
  fi
  sleep 0.5
done
if [ -n "$(ap_pids_listening_on_port "${BACKEND_PORT}")" ]; then
  echo "[start] 错误: 端口 ${BACKEND_PORT} 仍被占用，无法启动" >&2
  exit 1
fi

BACKEND_LOG="${LOG_DIR}/backend.log"

if [ "${FOREGROUND}" -eq 1 ]; then
  echo "[start] 前台启动后端 http://${AP_BIND_HOST}:${BACKEND_PORT}"
  ap_run_backend "${BACKEND_PORT}"
fi

echo "[start] 后台启动后端 http://${AP_BIND_HOST}:${BACKEND_PORT}"
nohup bash -c "
  # shellcheck disable=SC1091
  source '${SCRIPT_DIR}/lib/common.sh'
  ap_run_backend '${BACKEND_PORT}'
" > "${BACKEND_LOG}" 2>&1 &
BACKEND_PID=$!
echo "[start] 后端 PID=${BACKEND_PID}  日志: ${BACKEND_LOG}"

HEALTH_OK=0
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${BACKEND_PORT}/health" >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 1
done
if [ "${HEALTH_OK}" -eq 1 ]; then
  echo "[start] 健康检查通过: http://127.0.0.1:${BACKEND_PORT}/health"
else
  echo "[start] 健康检查未通过（已等待 30s），请查看日志: tail -f ${BACKEND_LOG}" >&2
fi

echo "[start] 查看状态: bash scripts/status.sh"
echo "[start] 停止服务: bash scripts/stop.sh"
