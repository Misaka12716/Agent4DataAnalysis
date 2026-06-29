#!/usr/bin/env bash
# Cube Sandbox 一键诊断（控制面 + 数据面 + Sandbox.create）
# 用法: bash scripts/diagnose-cube-sandbox.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

echo "[diagnose] 工作目录: ${ROOT}"
echo "[diagnose] 取消 HTTP 代理..."
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy || true

if command -v conda >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null || true
  conda activate agentPlatform 2>/dev/null || true
fi

PYTHON="${PYTHON:-python3}"
if ! "${PYTHON}" -c "import e2b_code_interpreter" 2>/dev/null; then
  echo "[diagnose] 警告: 未找到 e2b_code_interpreter，请先 conda activate agentPlatform" >&2
fi

exec "${PYTHON}" "${ROOT}/scripts/diagnose-cube-sandbox.py" "$@"
