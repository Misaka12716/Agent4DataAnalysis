#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

ENV_FILE="${ONE_CLICK_ENV_FILE:-${SCRIPT_DIR}/.env}"
if [[ -f "${ENV_FILE}" ]]; then
  load_env_file "${ENV_FILE}"
fi

require_root

TOOLBOX_ROOT="${ONE_CLICK_TOOLBOX_ROOT:-/usr/local/services/cubetoolbox}"
INSTALL_PREFIX="${ONE_CLICK_INSTALL_PREFIX:-${TOOLBOX_ROOT}}"

ensure_file "${INSTALL_PREFIX}/scripts/one-click/quickcheck.sh"
ONE_CLICK_TOOLBOX_ROOT="${INSTALL_PREFIX}" \
ONE_CLICK_RUNTIME_ENV_FILE="${INSTALL_PREFIX}/.one-click.env" \
  "${INSTALL_PREFIX}/scripts/one-click/quickcheck.sh"
