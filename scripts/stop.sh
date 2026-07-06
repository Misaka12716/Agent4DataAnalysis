#!/usr/bin/env bash
# 停止 AgentPlatform 后端 / 联调前端
# 用法:
#   bash scripts/stop.sh          # 仅停后端
#   bash scripts/stop.sh --all      # 后端 + 联调前端

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

STOP_ALL=0

for arg in "$@"; do
  case "${arg}" in
    --all) STOP_ALL=1 ;;
    -h|--help)
      cat <<EOF
用法: bash scripts/stop.sh [选项]

选项:
  --all       同时停止后端与联调前端
  -h, --help  显示此帮助
EOF
      exit 0
      ;;
    *)
      echo "[stop] 未知参数: ${arg}（可用 --help 查看）" >&2
      exit 1
      ;;
  esac
done

BACKEND_PORT="$(ap_backend_port)"

echo "[stop] 停止后端 (端口 ${BACKEND_PORT})..."
ap_stop_backend

if [ "${STOP_ALL}" -eq 1 ]; then
  echo "[stop] 停止联调前端..."
  ap_stop_frontend
fi

sleep 1

if ap_backend_pgrep | grep -q .; then
  echo "[stop] 警告: 后端进程可能仍在运行" >&2
  ap_backend_pgrep
else
  echo "[stop] 后端已停止"
fi

if [ "${STOP_ALL}" -eq 1 ]; then
  if ap_frontend_pgrep | grep -q .; then
    echo "[stop] 警告: 前端进程可能仍在运行" >&2
    ap_frontend_pgrep
  else
    echo "[stop] 联调前端已停止"
  fi
fi
