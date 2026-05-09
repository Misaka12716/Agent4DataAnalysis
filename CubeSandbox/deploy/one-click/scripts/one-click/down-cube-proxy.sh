#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"
# shellcheck source=./compose-lib.sh
source "${SCRIPT_DIR}/compose-lib.sh"

require_root
require_cmd docker

PROXY_DIR="${TOOLBOX_ROOT}/cubeproxy"
CUBE_PROXY_CONTAINER_NAME="${CUBE_PROXY_CONTAINER_NAME:-cube-proxy}"

if [[ -f "${PROXY_DIR}/docker-compose.yaml" ]]; then
  compose_run down --remove-orphans >/dev/null 2>&1 || true
fi

if container_exists "${CUBE_PROXY_CONTAINER_NAME}"; then
  docker rm -f "${CUBE_PROXY_CONTAINER_NAME}" >/dev/null
fi

log "cube proxy stopped"
