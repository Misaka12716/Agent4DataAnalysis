#!/usr/bin/env bash
# Fix Cube Sandbox MySQL host port conflict (default: 3309).
# Usage: sudo bash scripts/fix-cube-mysql-port.sh [PORT]
set -euo pipefail

MYSQL_HOST_PORT="${1:-3309}"
TOOLBOX="/usr/local/services/cubetoolbox"
TEMPLATE="${TOOLBOX}/support/docker-compose.yaml.template"
CONF="${TOOLBOX}/CubeMaster/conf.yaml"
ENV_FILE="${TOOLBOX}/.one-click.env"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run as root: sudo bash $0 [PORT]" >&2
  exit 1
fi

if ss -tln | grep -q ":${MYSQL_HOST_PORT} "; then
  echo "ERROR: port ${MYSQL_HOST_PORT} is already in use. Pick another port." >&2
  ss -tlnp | grep ":${MYSQL_HOST_PORT} " || true
  exit 1
fi

echo "[fix] Using host port ${MYSQL_HOST_PORT} for cube-sandbox-mysql"

# Update compose template (handle placeholder or prior hardcoded port)
sed -i \
  -e "s|127.0.0.1:__MYSQL_PORT__:3306|127.0.0.1:${MYSQL_HOST_PORT}:3306|g" \
  -e "s|127.0.0.1:330[0-9]*:3306|127.0.0.1:${MYSQL_HOST_PORT}:3306|g" \
  "${TEMPLATE}"

grep "${MYSQL_HOST_PORT}" "${TEMPLATE}" | head -1

# Update CubeMaster DB addresses (any prior 330x host port -> new port)
sed -i -E "s|127.0.0.1:330[0-9]+|127.0.0.1:${MYSQL_HOST_PORT}|g" "${CONF}"
grep "addr:" "${CONF}" | head -2

# Persist port in runtime env for install scripts
touch "${ENV_FILE}"
if grep -q '^CUBE_SANDBOX_MYSQL_PORT=' "${ENV_FILE}"; then
  sed -i "s/^CUBE_SANDBOX_MYSQL_PORT=.*/CUBE_SANDBOX_MYSQL_PORT=${MYSQL_HOST_PORT}/" "${ENV_FILE}"
else
  echo "CUBE_SANDBOX_MYSQL_PORT=${MYSQL_HOST_PORT}" >> "${ENV_FILE}"
fi
grep '^CUBE_SANDBOX_MYSQL_PORT=' "${ENV_FILE}"

# Re-render compose and restart infrastructure
rm -f "${TOOLBOX}/support/docker-compose.yaml"
docker rm -f cube-sandbox-mysql 2>/dev/null || true

systemctl restart cube-sandbox-mysql.service
sleep 5
systemctl is-active cube-sandbox-mysql.service

systemctl start cube-sandbox-control.target || true
sleep 8

echo "[fix] Port check:"
ss -tlnp | grep ":${MYSQL_HOST_PORT} " || true

echo "[fix] Health checks:"
curl -sf --noproxy '*' "http://127.0.0.1:8089/notify/health" && echo " cubemaster OK" || echo " cubemaster FAIL"
curl -sf --noproxy '*' "http://127.0.0.1:3000/health" && echo " cube-api OK" || echo " cube-api FAIL"

echo "[fix] Done."
