#!/usr/bin/env bash
# 本地 Runtime 诊断：工作区路径、python3、示例 write/run
# 用法: bash scripts/diagnose-runtime.sh [session_id]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

echo "[runtime] 工作目录: ${ROOT}"
echo "[runtime] CUBE_SANDBOX_ENABLED=${CUBE_SANDBOX_ENABLED:-0} (默认本地 Runtime)"

PYTHON="${PYTHON:-python3}"
export PYTHONPATH="${ROOT}/src:${PYTHONPATH:-}"

SID="${1:-diag-$(date +%s)}"
echo "[runtime] 测试 session_id=${SID}"

exec "${PYTHON}" "${ROOT}/scripts/diagnose-runtime.py" "${SID}"
