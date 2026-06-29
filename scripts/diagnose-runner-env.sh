#!/usr/bin/env bash
# 诊断 Runner 执行环境（RUNNER_PYTHON / pandas 等）
# 用法: bash scripts/diagnose-runner-env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PYTHON="${RUNNER_PYTHON:-}"
if [[ -z "${PYTHON}" ]]; then
  if command -v conda >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null || true
    if conda env list | awk '{print $1}' | grep -qx "agentPlatform-runner"; then
      PYTHON="$(conda run -n agentPlatform-runner which python 2>/dev/null || true)"
    fi
  fi
fi
PYTHON="${PYTHON:-python3}"

echo "[runner-env] RUNNER_PYTHON=${PYTHON}"
if [[ ! -x "${PYTHON}" ]] && ! command -v "${PYTHON}" >/dev/null 2>&1; then
  echo "[runner-env] 错误: 解释器不可执行。请先 bash scripts/setup-runner-env.sh" >&2
  exit 1
fi

"${PYTHON}" - <<'PY'
import sys
print(f"[runner-env] version={sys.version.split()[0]}")
print(f"[runner-env] executable={sys.executable}")

checks = ["numpy", "pandas", "openpyxl", "matplotlib", "sklearn"]
failed = []
for mod in checks:
    try:
        __import__(mod)
        print(f"[runner-env] import {mod}: OK")
    except ImportError as e:
        print(f"[runner-env] import {mod}: FAIL ({e})")
        failed.append(mod)

if failed:
    raise SystemExit(1)
print("[runner-env] OK — Runner 环境可用")
PY
