# 启动指令

## 前置条件

1. **MySQL 已就绪**（会话持久化依赖）。
2. **默认使用本地 Runtime**（`tmp/workspaces/<user_id>/<session_id>/`），无需 Cube Sandbox 即可创建会话、上传与分析。
3. **双 Python 环境**：`agentPlatform`（后端服务）与 `agentPlatform-runner`（Agent 生成代码的执行环境，相互隔离）。
4. 若需可选 Cube Sandbox 隔离执行，见下方「Cube Sandbox 配置（可选）」。

## 配置环境并安装依赖

### 1) 主服务环境（FastAPI / LangGraph / Reader）

```
conda create -n agentPlatform python=3.13.7
conda activate agentPlatform
python -m pip install -r requirements.txt
```

### 2) Runner 执行环境（Worker 运行的 Python 代码）

```bash
bash scripts/setup-runner-env.sh
export RUNNER_PYTHON="$(conda run -n agentPlatform-runner which python)"
bash scripts/diagnose-runner-env.sh
```

> Worker 通过 `RUNNER_PYTHON` 调用独立解释器，不会使用主环境中的 langchain/fastapi 等包。

## 本地 Runtime（默认）

执行层位于 [`src/runtime/`](../src/runtime/)，通过 `ensure_runtime(session_id)` 统一读写文件与运行 `python3` 脚本。

- **工作区路径**：`tmp/workspaces/<user_id>/<session_id>/`
- **代码执行**：Worker 使用 `RUNNER_PYTHON`（`agentPlatform-runner`），与 FastAPI 主环境隔离
- 默认 `CUBE_SANDBOX_ENABLED=0`（见 [`src/runtime/config.py`](../src/runtime/config.py)）

**诊断脚本**：

```bash
bash scripts/diagnose-runner-env.sh   # Runner 环境（pandas 等）
bash scripts/diagnose-runtime.sh      # 工作区 write/run
```

## Cube Sandbox 配置（可选）

仅在需要 MicroVM 隔离时启用：设置 `CUBE_SANDBOX_ENABLED=1` 并部署 Cube（见 [`Cubesandbox-deploy.md`](Cubesandbox-deploy.md)）。

启动前确认 Cube API 可达：

```bash
curl --noproxy '*' http://127.0.0.1:3000/health
```

**沙箱故障一键诊断**（启用沙箱且创建会话失败时）：

```bash
bash scripts/diagnose-cube-sandbox.sh
```

### 需要覆盖默认值时

- **改代码**：编辑 [`src/sandbox/config.py`](../src/sandbox/config.py) 或 [`src/runtime/config.py`](../src/runtime/config.py)。
- **改环境变量**：启动前 `export`，或复制 [`.env.example`](../.env.example) 后手动 `source`/export。注意：**后端不会自动加载 `.env`**。

常用覆盖示例：

```bash
export RUNNER_PYTHON=/path/to/agentPlatform-runner/bin/python
export CUBE_SANDBOX_ENABLED=1
export CUBE_TEMPLATE_ID=<你的模板ID>
```

详见 [`Cubesandbox-agent-integration.md`](Cubesandbox-agent-integration.md)。

## 启动后端指令

```bash
cd src
# 避免 HTTP/SOCKS 代理导致 E2B 502 或短信网关 Missing SOCKS support
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy SOCKS_PROXY socks_proxy SOCKS5_PROXY socks5_proxy
# Runner 执行环境（与主服务分离）
export RUNNER_PYTHON="$(conda run -n agentPlatform-runner which python)"
# 生产环境务必设置 JWT 签名密钥；本地开发可省略（将使用临时 dev 密钥）
# export JWT_SECRET_KEY="your-production-secret"
python -m uvicorn backend.server:app --host 0.0.0.0 --port 52716
```

## 启动前端指令

```
cd src
streamlit run frontend/frontend.py
```

## 相关文档

- Cube Sandbox 部署：[`Cubesandbox-deploy.md`](Cubesandbox-deploy.md)
- 与 AgentPlatform 集成：[`Cubesandbox-agent-integration.md`](Cubesandbox-agent-integration.md)
- 后端 API：[`BackendAPI.md`](BackendAPI.md)
