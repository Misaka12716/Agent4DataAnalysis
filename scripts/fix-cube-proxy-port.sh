#!/usr/bin/env bash
# 修复 CubeProxy 因 80/443 端口冲突无法启动的问题。
# 用法: sudo bash scripts/fix-cube-proxy-port.sh [HTTPS_PORT] [HTTP_PORT]
# 示例: sudo bash scripts/fix-cube-proxy-port.sh 8443 8080
set -euo pipefail

HTTPS_PORT="${1:-8443}"
HTTP_PORT="${2:-8080}"
TOOLBOX="/usr/local/services/cubetoolbox"
ENV_FILE="${TOOLBOX}/.one-click.env"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: 请以 root 运行: sudo bash $0 [HTTPS_PORT] [HTTP_PORT]" >&2
  exit 1
fi

port_free() {
  local p="$1"
  ! ss -tln | grep -q ":${p} "
}

echo "[fix] 检查端口 ${HTTPS_PORT} (HTTPS) 与 ${HTTP_PORT} (HTTP)..."
for p in "${HTTPS_PORT}" "${HTTP_PORT}"; do
  if ! port_free "${p}"; then
    echo "ERROR: 端口 ${p} 已被占用:" >&2
    ss -tlnp | grep ":${p} " || true
    exit 1
  fi
done

mkdir -p "$(dirname "${ENV_FILE}")"
touch "${ENV_FILE}"

set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i "s/^${key}=.*/${key}=${val}/" "${ENV_FILE}"
  else
    echo "${key}=${val}" >> "${ENV_FILE}"
  fi
}

set_env "CUBE_PROXY_HTTPS_PORT" "${HTTPS_PORT}"
set_env "CUBE_PROXY_HTTP_PORT" "${HTTP_PORT}"

echo "[fix] 已写入 ${ENV_FILE}:"
grep -E '^CUBE_PROXY_(HTTPS|HTTP)_PORT=' "${ENV_FILE}" || true

echo "[fix] 重启 cube-proxy..."
systemctl restart cube-sandbox-cube-proxy.service
sleep 3
systemctl is-active cube-sandbox-cube-proxy.service || {
  echo "ERROR: cube-proxy 仍未 active，请 journalctl -u cube-sandbox-cube-proxy.service -n 50" >&2
  exit 1
}

echo "[fix] 重启 network-agent 与 cubemaster..."
systemctl restart cube-sandbox-network-agent.service
systemctl restart cube-sandbox-cubemaster.service

echo "[fix] 端口监听:"
ss -tlnp | grep -E ":${HTTPS_PORT} |:${HTTP_PORT} " || true

echo "[fix] 完成。请运行: bash scripts/diagnose-cube-sandbox.sh"
