#!/usr/bin/env bash
# 创建 agentPlatform-runner 独立执行环境
# 用法: bash scripts/setup-runner-env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME="${RUNNER_CONDA_ENV:-agentPlatform-runner}"
PYTHON_VERSION="${RUNNER_PYTHON_VERSION:-3.13.7}"

echo "[setup-runner] 工作目录: ${ROOT}"
echo "[setup-runner] conda 环境: ${ENV_NAME} (python ${PYTHON_VERSION})"

if ! command -v conda >/dev/null 2>&1; then
  echo "[setup-runner] 错误: 未找到 conda，请先安装 Miniconda/Anaconda" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  echo "[setup-runner] 环境已存在，跳过 conda create"
else
  conda create -y -n "${ENV_NAME}" "python=${PYTHON_VERSION}"
fi

conda activate "${ENV_NAME}"
python -m pip install --upgrade pip
python -m pip install -r "${ROOT}/requirements-runner.txt"

RUNNER_PY="$(conda run -n "${ENV_NAME}" which python)"
echo ""
echo "[setup-runner] 完成。请将以下命令加入启动脚本或 shell profile:"
echo "export RUNNER_PYTHON=\"${RUNNER_PY}\""
