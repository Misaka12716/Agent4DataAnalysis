#!/usr/bin/env bash
# 平台初始化：生成演示 fixtures + 导入模板种子数据
# 用法:
#   bash scripts/init-platform.sh
#   bash scripts/init-platform.sh --acceptance   # 含验收用户/演示会话

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

ROOT="$(ap_root)"
SEED_SCRIPT="${ROOT}/scripts/seed_templates.py"
ACCEPTANCE_FLAG=""

for arg in "$@"; do
  case "${arg}" in
    --acceptance) ACCEPTANCE_FLAG="--acceptance" ;;
    -h|--help)
      cat <<EOF
用法: bash scripts/init-platform.sh [选项]

选项:
  --acceptance   写入验收用户 13800000000 与演示会话
  -h, --help     显示此帮助
EOF
      exit 0
      ;;
    *)
      echo "[init-platform] 未知参数: ${arg}" >&2
      exit 1
      ;;
  esac
done

if [ ! -f "${SEED_SCRIPT}" ]; then
  echo "[init-platform] 错误: 未找到 ${SEED_SCRIPT}" >&2
  exit 1
fi

ap_activate_conda

echo "[init-platform] 工作目录: ${ROOT}"
echo "[init-platform] 生成 fixtures 并导入模板..."
python "${SEED_SCRIPT}" ${ACCEPTANCE_FLAG}

echo "[init-platform] 归属历史会话并确保个人默认项目..."
python "${ROOT}/scripts/bootstrap-projects.py"

echo ""
echo "[init-platform] 完成。下一步:"
echo "  bash scripts/start.sh                  # 仅后端"
echo "  bash scripts/start.sh --with-frontend  # 后端 + 联调前端"
if [ -n "${ACCEPTANCE_FLAG}" ]; then
  echo "  ACCEPTANCE_MODE=1 bash scripts/start.sh --with-frontend  # 启用验收一键登录"
fi
