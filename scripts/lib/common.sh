#!/usr/bin/env bash
# AgentPlatform 启动脚本公共函数
# 用法: source "$(dirname "$0")/lib/common.sh"  （在 scripts/ 下脚本中）

AP_DEFAULT_BACKEND_PORT="${AP_DEFAULT_BACKEND_PORT:-52716}"
AP_DEFAULT_FRONTEND_PORT="${AP_DEFAULT_FRONTEND_PORT:-8501}"
AP_CONDA_ENV="${AP_CONDA_ENV:-agentPlatform}"

ap_root() {
  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${lib_dir}/../.." && pwd
}

ap_backend_port() {
  echo "${BACKEND_PORT:-${AP_DEFAULT_BACKEND_PORT}}"
}

ap_frontend_port() {
  echo "${FRONTEND_PORT:-${AP_DEFAULT_FRONTEND_PORT}}"
}

ap_log_dir() {
  echo "$(ap_root)/tmp/logs"
}

ap_activate_conda() {
  local conda_base=""
  if command -v conda >/dev/null 2>&1; then
    conda_base="$(conda info --base 2>/dev/null || true)"
  fi
  if [ -z "${conda_base}" ] && [ -f /opt/miniconda/etc/profile.d/conda.sh ]; then
    conda_base="/opt/miniconda"
  fi
  if [ -z "${conda_base}" ] || [ ! -f "${conda_base}/etc/profile.d/conda.sh" ]; then
    echo "[ap] 错误: 未找到 conda，请先安装 Miniconda/Anaconda" >&2
    return 1
  fi
  # shellcheck disable=SC1091
  source "${conda_base}/etc/profile.d/conda.sh"
  conda activate "${AP_CONDA_ENV}" 2>/dev/null || {
    echo "[ap] 错误: 无法激活 conda 环境 '${AP_CONDA_ENV}'" >&2
    return 1
  }
}

# 向 PID 发信号；用 [u]vicorn 等字符类避免 pgrep/pkill 匹配自身命令行
ap_signal_pids() {
  local sig="$1"
  shift
  local pid
  for pid in "$@"; do
    [ -n "${pid}" ] || continue
    kill "-${sig}" "${pid}" 2>/dev/null || true
  done
}

ap_pids_listening_on_port() {
  local port="$1"
  local pids=""
  if command -v fuser >/dev/null 2>&1; then
    # fuser 输出形如 "52716/tcp: 1234 5678"
    pids="$(fuser "${port}/tcp" 2>/dev/null | tr -s '[:space:]' '\n' | grep -E '^[0-9]+$' || true)"
  fi
  if [ -z "${pids}" ] && command -v ss >/dev/null 2>&1; then
    pids="$(ss -tlnp "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u || true)"
  fi
  if [ -z "${pids}" ] && command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)"
  fi
  echo "${pids}"
}

ap_backend_pids() {
  local port
  port="$(ap_backend_port)"
  # [u]vicorn：模式不会匹配含字面量 "[u]vicorn" 的 pgrep 自身
  pgrep -f "[u]vicorn backend.server:app.*--port[= ]${port}" 2>/dev/null || true
}

ap_stop_backend() {
  local port pid pids waited
  port="$(ap_backend_port)"

  pids="$(ap_backend_pids)"
  # 并入仍占用端口的进程（僵死/非标准启动路径）
  pids="$(printf '%s\n%s\n' "${pids}" "$(ap_pids_listening_on_port "${port}")" | grep -E '^[0-9]+$' | sort -u || true)"

  if [ -n "${pids}" ]; then
    # shellcheck disable=SC2086
    ap_signal_pids TERM ${pids}
  fi

  waited=0
  while [ "${waited}" -lt 10 ]; do
    pids="$(ap_backend_pids)"
    pids="$(printf '%s\n%s\n' "${pids}" "$(ap_pids_listening_on_port "${port}")" | grep -E '^[0-9]+$' | sort -u || true)"
    [ -z "${pids}" ] && return 0
    sleep 0.5
    waited=$((waited + 1))
  done

  # 仍未退出则强杀
  pids="$(ap_backend_pids)"
  pids="$(printf '%s\n%s\n' "${pids}" "$(ap_pids_listening_on_port "${port}")" | grep -E '^[0-9]+$' | sort -u || true)"
  if [ -n "${pids}" ]; then
    # shellcheck disable=SC2086
    ap_signal_pids KILL ${pids}
    sleep 0.5
  fi
}

ap_stop_frontend() {
  local pid
  for pid in $(pgrep -f "[s]treamlit run frontend/(app|frontend)\\.py" 2>/dev/null || true); do
    kill "${pid}" 2>/dev/null || true
  done
  for pid in $(pgrep -f "[s]treamlit run frontend/pages/template_analysis.py" 2>/dev/null || true); do
    kill "${pid}" 2>/dev/null || true
  done
}

ap_backend_pgrep() {
  local port pid
  port="$(ap_backend_port)"
  for pid in $(ap_backend_pids); do
    ps -p "${pid}" -o pid=,args= 2>/dev/null || true
  done
}

ap_frontend_pgrep() {
  pgrep -af "[s]treamlit run frontend/app.py" 2>/dev/null || true
}

ap_ensure_streamlit_config() {
  local root streamlit_dir
  root="$(ap_root)"
  streamlit_dir="${root}/src/.streamlit"
  mkdir -p "${streamlit_dir}"
  if [ ! -f "${streamlit_dir}/config.toml" ]; then
    cat > "${streamlit_dir}/config.toml" <<'EOF'
[browser]
gatherUsageStats = false

[server]
headless = true
address = "127.0.0.1"
EOF
  fi
}

ap_run_backend() {
  local port bind_host root
  port="${1:-$(ap_backend_port)}"
  bind_host="${AP_BIND_HOST:-0.0.0.0}"
  root="$(ap_root)"
  export ACCEPTANCE_MODE="${ACCEPTANCE_MODE:-0}"
  ap_activate_conda
  cd "${root}/src"
  echo "[ap] 启动后端 http://${bind_host}:${port} (ACCEPTANCE_MODE=${ACCEPTANCE_MODE})"
  exec python3 -m uvicorn backend.server:app --host "${bind_host}" --port "${port}"
}

ap_detect_lan_host() {
  hostname -I 2>/dev/null | awk '{print $1}'
}

ap_run_frontend() {
  local port bind_host root lan_host backend_port
  port="${1:-$(ap_frontend_port)}"
  bind_host="${AP_BIND_HOST:-0.0.0.0}"
  backend_port="$(ap_backend_port)"
  lan_host="${AP_PUBLIC_HOST:-$(ap_detect_lan_host)}"
  lan_host="${lan_host:-127.0.0.1}"
  root="$(ap_root)"
  export AP_PUBLIC_HOST="${lan_host}"
  export BACKEND_PORT="${backend_port}"
  export API_BASE="http://${lan_host}:${backend_port}"
  ap_activate_conda
  ap_ensure_streamlit_config
  cd "${root}/src"
  echo "[ap] 启动联调前端 http://${bind_host}:${port}"
  echo "[ap] 浏览器访问: http://${lan_host}:${port}/render_clinical_support_page"
  echo "[ap] 默认后端 API: ${API_BASE}"
  exec python3 -m streamlit run frontend/app.py \
    --server.port "${port}" \
    --server.address "${bind_host}" \
    --server.headless true
}
