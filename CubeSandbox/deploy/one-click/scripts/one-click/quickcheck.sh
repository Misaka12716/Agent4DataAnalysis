#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./common.sh
source "${SCRIPT_DIR}/common.sh"

TOOLBOX_ROOT="${ONE_CLICK_TOOLBOX_ROOT:-/usr/local/services/cubetoolbox}"
MASTER_ADDR="$(resolve_control_plane_cubemaster_addr)"
NA_HEALTH_ADDR="${NETWORK_AGENT_HEALTH_ADDR:-127.0.0.1:19090}"
CUBE_API_HEALTH_ADDR="${CUBE_API_HEALTH_ADDR:-127.0.0.1:3000}"
ROLE="$(one_click_deploy_role)"
NODE_ID="${CUBE_SANDBOX_NODE_IP:-}"

echo "[quickcheck] cubemaster=${MASTER_ADDR}"
echo "[quickcheck] network-agent-health=${NA_HEALTH_ADDR}"
if [[ "${ROLE}" != "compute" ]]; then
  echo "[quickcheck] cube-api-health=${CUBE_API_HEALTH_ADDR}"
fi

echo "[quickcheck] 1/5 check network-agent healthz"
curl -fsS "http://${NA_HEALTH_ADDR}/healthz" >/dev/null

echo "[quickcheck] 2/5 check network-agent readyz"
curl -fsS "http://${NA_HEALTH_ADDR}/readyz" >/dev/null

echo "[quickcheck] 3/5 check cubemaster /notify/health"
curl -fsS "http://${MASTER_ADDR}/notify/health" >/dev/null

if [[ "${ROLE}" == "compute" ]]; then
  [[ -n "${NODE_ID}" ]] || die "CUBE_SANDBOX_NODE_IP is required for compute quickcheck"
  echo "[quickcheck] 4/5 check cubemaster node registration"
  curl -fsS "http://${MASTER_ADDR}/internal/meta/nodes/${NODE_ID}" | rg -q "\"host_ip\":\"${NODE_ID}\""

  echo "[quickcheck] 5/5 check essential sockets and runtime assets"
  test -S "/data/cubelet/cubelet.sock"
  test -S "/tmp/cube/network-agent-grpc.sock"
  test -f "${TOOLBOX_ROOT}/Cubelet/config/config.toml"
  test -f "${TOOLBOX_ROOT}/Cubelet/dynamicconf/conf.yaml"
  test -f "${TOOLBOX_ROOT}/cube-shim/conf/config-cube.toml"
  test -f "${TOOLBOX_ROOT}/cube-kernel-scf/vmlinux"
  test -f "${TOOLBOX_ROOT}/cube-image/cube-guest-image-cpu.img"
else
  echo "[quickcheck] 4/5 check cube-api /health"
  curl -fsS "http://${CUBE_API_HEALTH_ADDR}/health" >/dev/null

  echo "[quickcheck] 5/5 check essential sockets and config"
  test -S "/data/cubelet/cubelet.sock"
  test -S "/tmp/cube/network-agent-grpc.sock"
  test -x "${TOOLBOX_ROOT}/CubeAPI/bin/cube-api"
  test -f "${TOOLBOX_ROOT}/CubeMaster/conf.yaml"
  test -f "${TOOLBOX_ROOT}/Cubelet/config/config.toml"
  test -f "${TOOLBOX_ROOT}/Cubelet/dynamicconf/conf.yaml"
  test -f "${TOOLBOX_ROOT}/cube-shim/conf/config-cube.toml"
fi

echo "[quickcheck] OK"
