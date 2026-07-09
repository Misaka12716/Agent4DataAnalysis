#!/usr/bin/env bash
# 迁移 legacy 会话工作区到 project/sessions/ 布局
# 用法:
#   bash scripts/migrate-legacy-sessions.sh --dry-run
#   bash scripts/migrate-legacy-sessions.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

ROOT="$(ap_root)"
ap_activate_conda

cd "${ROOT}"
python scripts/migrate-legacy-sessions.py "$@"
