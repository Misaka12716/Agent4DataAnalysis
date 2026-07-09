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

ap_stop_backend() {
  local port
  port="$(ap_backend_port)"
  pkill -f "backend.server:app.*--port ${port}" 2>/dev/null || true
  pkill -f "backend.server:app.*--port=${port}" 2>/dev/null || true
}

ap_stop_frontend() {
  pkill -f "streamlit run frontend/app.py" 2>/dev/null || true
  pkill -f "streamlit run frontend/frontend.py" 2>/dev/null || true
  pkill -f "streamlit run frontend/pages/template_analysis.py" 2>/dev/null || true
}

ap_backend_pgrep() {
  local port
  port="$(ap_backend_port)"
  pgrep -af "backend.server:app.*--port ${port}" 2>/dev/null || true
}

ap_frontend_pgrep() {
  pgrep -af "streamlit run frontend/app.py" 2>/dev/null || true
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

ap_run_frontend() {
  local port bind_host root
  port="${1:-$(ap_frontend_port)}"
  bind_host="${AP_BIND_HOST:-0.0.0.0}"
  root="$(ap_root)"
  ap_activate_conda
  ap_ensure_streamlit_config
  cd "${root}/src"
  echo "[ap] 启动联调前端 http://${bind_host}:${port}"
  exec python3 -m streamlit run frontend/app.py \
    --server.port "${port}" \
    --server.address "${bind_host}" \
    --server.headless true
}
