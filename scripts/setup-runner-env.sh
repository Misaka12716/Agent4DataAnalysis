#!/usr/bin/env bash
# 创建/补齐 Worker 专用 conda 环境 agentPlatform-runner，并安装 requirements-runner.txt
# 注意：conda base 不可改名；本脚本创建独立命名环境。
# 默认装到用户 envs 目录（/ 盘常满时勿装到 /opt/miniconda/envs）。
# 用法: bash scripts/setup-runner-env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME="${RUNNER_CONDA_ENV:-agentPlatform-runner}"
PYTHON_VERSION="${RUNNER_PYTHON_VERSION:-3.9}"
# 优先显式前缀；默认放用户 .conda/envs，避免占满 /
ENV_PREFIX="${RUNNER_ENV_PREFIX:-${HOME}/.conda/envs/${ENV_NAME}}"

echo "[setup-runner] 工作目录: ${ROOT}"
echo "[setup-runner] conda 环境: ${ENV_PREFIX} (python ${PYTHON_VERSION})"

if ! command -v conda >/dev/null 2>&1; then
  echo "[setup-runner] 错误: 未找到 conda，请先安装 Miniconda/Anaconda" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"
export CONDA_PKGS_DIRS="${CONDA_PKGS_DIRS:-${HOME}/.conda/pkgs}"
mkdir -p "${CONDA_PKGS_DIRS}" "$(dirname "${ENV_PREFIX}")"

if [[ -x "${ENV_PREFIX}/bin/python" ]]; then
  echo "[setup-runner] 环境已存在，跳过 conda create: ${ENV_PREFIX}"
else
  conda create -y -p "${ENV_PREFIX}" "python=${PYTHON_VERSION}"
fi

"${ENV_PREFIX}/bin/python" -m pip install --upgrade pip
"${ENV_PREFIX}/bin/python" -m pip install -r "${ROOT}/requirements-runner.txt"

echo ""
echo "[setup-runner] 完成。请写入仓库根 .env："
echo "RUNNER_PYTHON=${ENV_PREFIX}/bin/python"
