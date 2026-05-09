#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"
# shellcheck source=./support-compose-lib.sh
source "${SCRIPT_DIR}/support-compose-lib.sh"

require_root
require_cmd docker

REMOVE_VOLUMES="${CUBE_SANDBOX_REMOVE_VOLUMES:-0}"
SUPPORT_DIR="${TOOLBOX_ROOT}/support"

if [[ -f "${SUPPORT_DIR}/docker-compose.yaml" ]]; then
  if [[ "${REMOVE_VOLUMES}" == "1" ]]; then
    support_compose_run down --remove-orphans -v >/dev/null 2>&1 || true
  else
    support_compose_run down --remove-orphans >/dev/null 2>&1 || true
  fi
fi

log "support services stopped"
