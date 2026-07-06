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

## 启动

在**仓库根目录**执行：

```bash
# 首次初始化（模板 + fixtures）
bash scripts/init-platform.sh

# 日常启动
bash scripts/start.sh                     # 仅后端 52716
bash scripts/start.sh --with-frontend     # 后端 + 联调前端 8501

# 查看 / 停止
bash scripts/status.sh
bash scripts/stop.sh              # 仅停后端
bash scripts/stop.sh --all        # 后端 + 联调前端
```

| 脚本 | 说明 |
|------|------|
| [`scripts/init-platform.sh`](../scripts/init-platform.sh) | 首次初始化：fixtures + 模板种子（`--acceptance` 含验收账号） |
| [`scripts/start.sh`](../scripts/start.sh) | 停旧进程并后台启动（`--with-frontend` / `--foreground`） |
| [`scripts/stop.sh`](../scripts/stop.sh) | 停止服务（`--all` 含前端） |
| [`scripts/status.sh`](../scripts/status.sh) | 进程与健康检查 |

日志目录：`tmp/logs/`（`backend.log`、`frontend.log`）。

健康检查：

```bash
curl http://localhost:52716/health
```

### 联调前端说明

| 项 | 说明 |
|----|------|
| 访问地址 | `http://<主机>:8501`（8501 仅作联调示例，正式运行可不开） |
| 验收登录 | 可选：`bash scripts/init-platform.sh --acceptance` + `ACCEPTANCE_MODE=1 bash scripts/start.sh --with-frontend`，侧栏 `13800000000` / `888888` |
| 分析入口 | 「模板分析」页选模板跑分析；「流式分析」页跑 LLM SSE |
| 正式前端 | 按 [`TemplateAPI.md`](TemplateAPI.md) 对接 HTTP；Streamlit 不是产品前端 |

**演示数据**：运行 `bash scripts/init-platform.sh` 后位于 `tests/fixtures/`（横截面 baseline 样本 + 纵向随访样本）

模板 API 详见 [`TemplateAPI.md`](TemplateAPI.md)。

## 相关文档

- 模板分析 API：[`TemplateAPI.md`](TemplateAPI.md)
- Cube Sandbox 部署：[`Cubesandbox-deploy.md`](Cubesandbox-deploy.md)
- 与 AgentPlatform 集成：[`Cubesandbox-agent-integration.md`](Cubesandbox-agent-integration.md)
- 后端 API：[`BackendAPI.md`](BackendAPI.md)
