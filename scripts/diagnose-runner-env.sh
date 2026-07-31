#!/usr/bin/env bash
# 诊断 Runner 执行环境（RUNNER_PYTHON / 数据分析依赖）
# 用法: bash scripts/diagnose-runner-env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# 优先进程环境；否则尝试从仓库根 .env 读取 RUNNER_PYTHON
PYTHON="${RUNNER_PYTHON:-}"
if [[ -z "${PYTHON}" && -f "${ROOT}/.env" ]]; then
  # shellcheck disable=SC1091
  PYTHON="$(grep -E '^[[:space:]]*RUNNER_PYTHON=' "${ROOT}/.env" | tail -n1 | cut -d= -f2- | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^"//;s/"$//;s/^'"'"'//;s/'"'"'$//' || true)"
fi
PYTHON="${PYTHON:-python3}"

echo "[runner-env] RUNNER_PYTHON=${PYTHON}"
if [[ ! -x "${PYTHON}" ]] && ! command -v "${PYTHON}" >/dev/null 2>&1; then
  echo "[runner-env] 错误: 解释器不可执行。请在 .env 设置 RUNNER_PYTHON，或 pip install -r requirements-runner.txt" >&2
  exit 1
fi

"${PYTHON}" - <<'PY'
import sys
print(f"[runner-env] version={sys.version.split()[0]}")
print(f"[runner-env] executable={sys.executable}")

checks = [
    "numpy",
    "pandas",
    "openpyxl",
    "matplotlib",
    "seaborn",
    "scipy",
    "sklearn",
    "joblib",
    "PIL",
    "networkx",
    "statsmodels",
    "xgboost",
    "lifelines",
]
# 可选：清单中有但当前解释器可能未装
optional = ["xlrd", "lightgbm"]

failed = []
for mod in checks:
    try:
        __import__(mod)
        print(f"[runner-env] import {mod}: OK")
    except ImportError as e:
        print(f"[runner-env] import {mod}: FAIL ({e})")
        failed.append(mod)

for mod in optional:
    try:
        __import__(mod)
        print(f"[runner-env] import {mod}: OK (optional)")
    except ImportError as e:
        print(f"[runner-env] import {mod}: MISSING (optional, see requirements-runner.txt) ({e})")

if failed:
    raise SystemExit(1)
print("[runner-env] OK — Runner 环境可用")
PY
