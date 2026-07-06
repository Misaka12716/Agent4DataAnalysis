#!/usr/bin/env bash
# 启动 AgentPlatform 后端（默认）；可选联调前端
# 用法:
#   bash scripts/start.sh
#   bash scripts/start.sh --with-frontend
#   bash scripts/start.sh --foreground
#   BACKEND_PORT=52716 FRONTEND_PORT=8501 bash scripts/start.sh --with-frontend

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

BACKEND_PORT="$(ap_backend_port)"
FRONTEND_PORT="$(ap_frontend_port)"
LOG_DIR="$(ap_log_dir)"

WITH_FRONTEND=0
FOREGROUND=0

for arg in "$@"; do
  case "${arg}" in
    --with-frontend) WITH_FRONTEND=1 ;;
    --foreground) FOREGROUND=1 ;;
    -h|--help)
      cat <<EOF
用法: bash scripts/start.sh [选项]

选项:
  --with-frontend   同时启动 Streamlit 联调前端（默认端口 ${AP_DEFAULT_FRONTEND_PORT}）
  --foreground      前台运行（不加 nohup，便于调试）
  -h, --help        显示此帮助

环境变量:
  BACKEND_PORT      后端端口（默认 ${AP_DEFAULT_BACKEND_PORT}）
  FRONTEND_PORT     前端端口（默认 ${AP_DEFAULT_FRONTEND_PORT}）
  AP_BIND_HOST      监听地址（默认 0.0.0.0；勿用 HOST，conda 会覆盖）
  ACCEPTANCE_MODE   验收模式（默认 0；本地联调可设为 1 启用侧栏一键登录）
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
if [ "${WITH_FRONTEND}" -eq 1 ]; then
  ap_stop_frontend
fi
sleep 2

BACKEND_LOG="${LOG_DIR}/backend.log"
FRONTEND_LOG="${LOG_DIR}/frontend.log"

if [ "${FOREGROUND}" -eq 1 ]; then
  echo "[start] 前台启动后端 http://${AP_BIND_HOST}:${BACKEND_PORT}"
  if [ "${WITH_FRONTEND}" -eq 1 ]; then
    echo "[start] 提示: --foreground 模式下仅启动后端；前端请另开终端: bash scripts/start.sh --with-frontend" >&2
  fi
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

if [ "${WITH_FRONTEND}" -eq 1 ]; then
  echo "[start] 后台启动联调前端 http://${AP_BIND_HOST}:${FRONTEND_PORT}"
  nohup bash -c "
    # shellcheck disable=SC1091
    source '${SCRIPT_DIR}/lib/common.sh'
    ap_run_frontend '${FRONTEND_PORT}'
  " > "${FRONTEND_LOG}" 2>&1 &
  FRONTEND_PID=$!
  echo "[start] 前端 PID=${FRONTEND_PID}  日志: ${FRONTEND_LOG}"
fi

sleep 2
if curl -sf "http://127.0.0.1:${BACKEND_PORT}/health" >/dev/null 2>&1; then
  echo "[start] 健康检查通过: http://127.0.0.1:${BACKEND_PORT}/health"
else
  echo "[start] 健康检查未通过，请查看日志: tail -f ${BACKEND_LOG}" >&2
fi

if [ "${WITH_FRONTEND}" -eq 1 ]; then
  echo "[start] 联调前端: http://${AP_BIND_HOST}:${FRONTEND_PORT}（验收登录 13800000000 / 888888）"
fi

echo "[start] 查看状态: bash scripts/status.sh"
echo "[start] 停止服务: bash scripts/stop.sh$([ "${WITH_FRONTEND}" -eq 1 ] && echo ' --all')"
