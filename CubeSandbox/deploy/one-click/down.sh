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
ensure_dir "${INSTALL_PREFIX}"

ROLE_FILE="${INSTALL_PREFIX}/.one-click.env"
ROLE="control"
if [[ -f "${ROLE_FILE}" ]]; then
  role_line="$(rg '^ONE_CLICK_DEPLOY_ROLE=' "${ROLE_FILE}" || true)"
  if [[ -n "${role_line}" ]]; then
    ROLE="${role_line#ONE_CLICK_DEPLOY_ROLE=}"
  fi
fi

if [[ "${ROLE}" == "compute" ]]; then
  ensure_file "${INSTALL_PREFIX}/scripts/one-click/down-compute.sh"
  stop_script="${INSTALL_PREFIX}/scripts/one-click/down-compute.sh"
else
  ensure_file "${INSTALL_PREFIX}/scripts/one-click/down-with-deps.sh"
  stop_script="${INSTALL_PREFIX}/scripts/one-click/down-with-deps.sh"
fi

ONE_CLICK_TOOLBOX_ROOT="${INSTALL_PREFIX}" \
ONE_CLICK_RUNTIME_ENV_FILE="${INSTALL_PREFIX}/.one-click.env" \
  "${stop_script}"
