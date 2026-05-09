#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  sudo ./deploy-manual.sh /path/to/cube-manual-update-*.tar.gz

Environment overrides:
  ONE_CLICK_TOOLBOX_ROOT     Toolbox root, default: /usr/local/services/cubetoolbox
  ONE_CLICK_INSTALL_PREFIX   Install prefix, default: same as toolbox root
  ONE_CLICK_RUNTIME_DIR      Runtime dir, default: /var/run/cube-sandbox-one-click
  ONE_CLICK_LOG_DIR          Log dir, default: /var/log/cube-sandbox-one-click
  ONE_CLICK_MANUAL_PACKAGE_TAR
                             Package path if positional arg is omitted
  ONE_CLICK_SKIP_QUICKCHECK  Set to 1 to skip quickcheck after restart

Behavior:
  - backup current cubemaster/cubemastercli/cubelet/cubecli/network-agent
  - extract package and replace binaries
  - restart local one-click core services
  - refresh cubelet pidfile
  - run quickcheck and print key status
EOF
}

resolve_package_path() {
  local arg_path="${1:-}"
  if [[ -n "${arg_path}" ]]; then
    printf '%s\n' "${arg_path}"
    return 0
  fi
  if [[ -n "${ONE_CLICK_MANUAL_PACKAGE_TAR:-}" ]]; then
    printf '%s\n' "${ONE_CLICK_MANUAL_PACKAGE_TAR}"
    return 0
  fi

  local candidate
  for candidate in \
    "${PWD}"/cube-manual-update-*.tar.gz \
    "${SCRIPT_DIR}"/cube-manual-update-*.tar.gz
  do
    if [[ -f "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

fix_cubelet_pidfile() {
  local install_prefix="$1"
  local runtime_dir="$2"
  local cubelet_pid
  cubelet_pid="$(pgrep -f "^${install_prefix}/Cubelet/bin/cubelet --config" | head -n 1 || true)"
  if [[ -z "${cubelet_pid}" ]]; then
    log "cubelet pid not found, skip pidfile refresh"
    return 0
  fi
  mkdir -p "${runtime_dir}"
  printf '%s\n' "${cubelet_pid}" > "${runtime_dir}/cubelet.pid"
  if [[ "${runtime_dir}" != "/run/cube-sandbox-one-click" ]] && [[ -d /run/cube-sandbox-one-click ]]; then
    printf '%s\n' "${cubelet_pid}" > /run/cube-sandbox-one-click/cubelet.pid
  fi
  if [[ "${runtime_dir}" != "/var/run/cube-sandbox-one-click" ]] && [[ -d /var/run/cube-sandbox-one-click ]]; then
    printf '%s\n' "${cubelet_pid}" > /var/run/cube-sandbox-one-click/cubelet.pid
  fi
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  require_root
  require_cmd tar
  require_cmd install
  require_cmd pgrep
  require_cmd curl

  local package_tar
  package_tar="$(resolve_package_path "${1:-}")" || die "manual update package not specified"
  ensure_file "${package_tar}"

  local toolbox_root="${ONE_CLICK_TOOLBOX_ROOT:-/usr/local/services/cubetoolbox}"
  local install_prefix="${ONE_CLICK_INSTALL_PREFIX:-${toolbox_root}}"
  local runtime_dir="${ONE_CLICK_RUNTIME_DIR:-/var/run/cube-sandbox-one-click}"
  local log_dir="${ONE_CLICK_LOG_DIR:-/var/log/cube-sandbox-one-click}"
  local backup_dir="${install_prefix}/.backup/manual-update-$(date +%Y%m%d-%H%M%S)"
  local work_dir
  work_dir="$(mktemp -d)"
  trap "rm -rf '${work_dir}'" EXIT

  ensure_dir "${install_prefix}"
  ensure_file "${install_prefix}/scripts/one-click/down-local.sh"
  ensure_file "${install_prefix}/scripts/one-click/up.sh"
  ensure_file "${install_prefix}/scripts/one-click/quickcheck.sh"

  mkdir -p "${backup_dir}"
  log "backup current binaries to ${backup_dir}"
  cp -a "${install_prefix}/CubeMaster/bin/cubemaster" "${backup_dir}/"
  cp -a "${install_prefix}/CubeMaster/bin/cubemastercli" "${backup_dir}/"
  cp -a "${install_prefix}/Cubelet/bin/cubelet" "${backup_dir}/"
  cp -a "${install_prefix}/Cubelet/bin/cubecli" "${backup_dir}/"
  cp -a "${install_prefix}/network-agent/bin/network-agent" "${backup_dir}/"

  log "extract package ${package_tar}"
  tar -xzf "${package_tar}" -C "${work_dir}"

  ensure_file "${work_dir}/cubemaster"
  ensure_file "${work_dir}/cubemastercli"
  ensure_file "${work_dir}/cubelet"
  ensure_file "${work_dir}/cubecli"
  ensure_file "${work_dir}/network-agent"

  log "replace binaries under ${install_prefix}"
  install -m 0755 "${work_dir}/cubemaster" "${install_prefix}/CubeMaster/bin/cubemaster"
  install -m 0755 "${work_dir}/cubemastercli" "${install_prefix}/CubeMaster/bin/cubemastercli"
  install -m 0755 "${work_dir}/cubelet" "${install_prefix}/Cubelet/bin/cubelet"
  install -m 0755 "${work_dir}/cubecli" "${install_prefix}/Cubelet/bin/cubecli"
  install -m 0755 "${work_dir}/network-agent" "${install_prefix}/network-agent/bin/network-agent"

  log "restart local services"
  ONE_CLICK_TOOLBOX_ROOT="${install_prefix}" \
  ONE_CLICK_RUNTIME_DIR="${runtime_dir}" \
  ONE_CLICK_LOG_DIR="${log_dir}" \
    "${install_prefix}/scripts/one-click/down-local.sh" || true

  ONE_CLICK_TOOLBOX_ROOT="${install_prefix}" \
  ONE_CLICK_RUNTIME_DIR="${runtime_dir}" \
  ONE_CLICK_LOG_DIR="${log_dir}" \
    "${install_prefix}/scripts/one-click/up.sh"

  fix_cubelet_pidfile "${install_prefix}" "${runtime_dir}"

  if [[ "${ONE_CLICK_SKIP_QUICKCHECK:-0}" != "1" ]]; then
    ONE_CLICK_TOOLBOX_ROOT="${install_prefix}" \
    ONE_CLICK_RUNTIME_DIR="${runtime_dir}" \
    ONE_CLICK_LOG_DIR="${log_dir}" \
      "${install_prefix}/scripts/one-click/quickcheck.sh"
  fi

  log "node metadata after restart"
  curl -fsS http://127.0.0.1:8089/internal/meta/nodes || true
  printf '\n'

  log "manual update complete"
  log "backup dir: ${backup_dir}"
}

main "$@"
