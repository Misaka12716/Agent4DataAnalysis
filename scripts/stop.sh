#!/usr/bin/env bash
# 停止 AgentPlatform 后端
# 用法: bash scripts/stop.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

BACKEND_PORT="$(ap_backend_port)"

echo "[stop] 停止后端 (端口 ${BACKEND_PORT})..."
ap_stop_backend

sleep 1

if ap_backend_pgrep | grep -q .; then
  echo "[stop] 警告: 后端进程可能仍在运行" >&2
  ap_backend_pgrep
else
  echo "[stop] 后端已停止"
fi
