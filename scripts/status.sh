#!/usr/bin/env bash
# 查看 AgentPlatform 后端 / 联调前端运行状态
# 用法: bash scripts/status.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

BACKEND_PORT="$(ap_backend_port)"
FRONTEND_PORT="$(ap_frontend_port)"
LOG_DIR="$(ap_log_dir)"

echo "=== AgentPlatform 服务状态 ==="
echo ""

echo "[后端] 端口 ${BACKEND_PORT}"
if ap_backend_pgrep | grep -q .; then
  ap_backend_pgrep | sed 's/^/  /'
  if curl -sf "http://127.0.0.1:${BACKEND_PORT}/health" 2>/dev/null; then
    echo ""
    echo "  健康检查: OK"
  else
    echo "  健康检查: 失败（进程在跑但 /health 无响应）"
  fi
else
  echo "  未运行"
fi

echo ""
echo "[联调前端] 端口 ${FRONTEND_PORT}"
if ap_frontend_pgrep | grep -q .; then
  ap_frontend_pgrep | sed 's/^/  /'
else
  echo "  未运行"
fi

echo ""
echo "[日志]"
echo "  后端: ${LOG_DIR}/backend.log"
echo "  前端: ${LOG_DIR}/frontend.log"
