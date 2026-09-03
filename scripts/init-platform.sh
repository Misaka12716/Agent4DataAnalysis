#!/usr/bin/env bash
# 平台初始化：建表 + 默认用户 + 默认项目 + 可选演示数据
# 用法:
#   bash scripts/init-platform.sh
#   bash scripts/init-platform.sh --demo

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

ROOT="$(ap_root)"
INIT_SCRIPT="${ROOT}/scripts/init-core.py"
DEMO_FLAG=""

for arg in "$@"; do
  case "${arg}" in
    --demo) DEMO_FLAG="--demo" ;;
    -h|--help)
      cat <<EOF
用法: bash scripts/init-platform.sh [选项]

选项:
  --demo         写入演示会话与示例数据文件
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

if [ ! -f "${INIT_SCRIPT}" ]; then
  echo "[init-platform] 错误: 未找到 ${INIT_SCRIPT}" >&2
  exit 1
fi

ap_activate_conda

echo "[init-platform] 工作目录: ${ROOT}"
python "${INIT_SCRIPT}" ${DEMO_FLAG}

echo ""
echo "[init-platform] 完成。下一步:"
echo "  bash scripts/start.sh"
