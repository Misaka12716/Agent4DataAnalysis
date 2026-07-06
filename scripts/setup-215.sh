#!/usr/bin/env bash
# 已弃用：请使用 bash scripts/init-platform.sh --acceptance
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[setup-215] 已弃用，转发到 init-platform.sh --acceptance" >&2
exec bash "${SCRIPT_DIR}/init-platform.sh" --acceptance
