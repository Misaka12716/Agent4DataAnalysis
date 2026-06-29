# AgentPlatform 与 Cube Sandbox 集成

本文说明 **可选** Cube Sandbox 后端如何通过 E2B Python SDK 接入统一 Runtime 层。

> **默认行为**：`CUBE_SANDBOX_ENABLED=0`，使用本地 Runtime + 独立 Runner 环境（`RUNNER_PYTHON` / `agentPlatform-runner`），无需部署 Cube。见 [StartInstruction.md](./StartInstruction.md)。

## 架构概览

上层统一调用 [`src/runtime/`](../src/runtime/)：

```text
ensure_runtime(session_id)
  ├─ 默认 → LocalRuntime（workspaces/<user_id>/<session_id>/ + RUNNER_PYTHON）
  └─ CUBE_SANDBOX_ENABLED=1 → SandboxRuntimeAdapter（Cube MicroVM）
         └─ 不可用时 factory 自动降级 LocalRuntime
```

- **每个 session 一个工作区**：路径 `tmp/workspaces/<user_id>/<session_id>/`；DB 存 `workspace_abs_path`。
- **沙箱模式**：MicroVM 内为真实存储；`SandboxRuntimeAdapter` 写文件后 sync 到本地镜像路径，Reader / workspace-tree 仍读本地目录。
- **分析结束**：`release_runtime(session_id)`；沙箱模式下内部 `pause_sandbox`。
- **sandbox_id** 持久化在 `{workspace}/.cube_sandbox_meta.json`，无需改 MySQL 表结构。

## 环境变量

复制根目录 [`.env.example`](../.env.example) 并按部署情况修改：

| 变量 | 说明 |
|------|------|
| `RUNNER_PYTHON` | 本地 Runtime 下 Worker 使用的 Python（与主服务分离） |
| `CUBE_SANDBOX_ENABLED` | `0` 本地 Runtime（**默认**）；`1` 尝试 Cube 沙箱 |
| `E2B_API_URL` | CubeAPI 地址，如 `http://127.0.0.1:3000` |
| `E2B_API_KEY` | 本地部署填任意非空字符串，如 `e2b_000000` |
| `CUBE_TEMPLATE_ID` | 模板 ID，如 `tpl-78c1861fc2b54381947d33e2` |
| `SANDBOX_WORKDIR` | 沙箱内工作根目录，默认 `/home/user` |
| `SANDBOX_TIMEOUT` | 沙箱 TTL（秒），默认 `600` |
| `SSL_CERT_FILE` | 使用 mkcert HTTPS 时设置 CA 路径 |

部署 Cube Sandbox 请参阅 [Cubesandbox-deploy.md](./Cubesandbox-deploy.md)；SDK 用法示例见 [Cubesandbox-using.md](./Cubesandbox-using.md)。

## 代码入口

| 模块 | 职责 |
|------|------|
| [`src/runtime/factory.py`](../src/runtime/factory.py) | `ensure_runtime` / `release_runtime`，选后端与降级 |
| [`src/runtime/sandbox_adapter.py`](../src/runtime/sandbox_adapter.py) | 沙箱适配器，包装 `sandbox/*` |
| [`src/sandbox/session_manager.py`](../src/sandbox/session_manager.py) | create / connect / pause |
| [`src/sandbox/files.py`](../src/sandbox/files.py) | 沙箱读写与 sync 到本地镜像 |
| [`src/utils/workspace_file_ops.py`](../src/utils/workspace_file_ops.py) | Coder 统一读写（经 runtime） |
| [`src/worker/workspace_worker.py`](../src/worker/workspace_worker.py) | Worker 统一执行（经 runtime） |

## 本地 Runtime（默认）

无需 Cube，安装主环境与 Runner 环境即可：

```bash
bash scripts/setup-runner-env.sh
export RUNNER_PYTHON="$(conda run -n agentPlatform-runner which python)"
export CUBE_SANDBOX_ENABLED=0
```

文件写入 `tmp/workspaces/<user_id>/<session_id>/`，Worker 使用 `RUNNER_PYTHON` 执行 `python3 <相对路径.py>`。

## 端到端操作清单（启用沙箱时验收）

1. **部署 Cube Sandbox** — 见 [Cubesandbox-deploy.md](./Cubesandbox-deploy.md)，控制面 `curl --noproxy '*' http://127.0.0.1:3000/health` 返回正常。
2. **制作模板** — `cubemastercli tpl create-from-image ...`，`tpl watch` 至 `READY`，记录 `template_id`。
3. **配置并启动 AgentPlatform** — 设置 `CUBE_SANDBOX_ENABLED=1` 与 E2B 相关变量，按 [StartInstruction.md](./StartInstruction.md) 启动后端（`unset` 代理）。
4. **创建会话并上传** — `POST /session/create` → `POST /session/upload-excel` 上传表格文件。
5. **发起分析** — `POST /run-analysis`，观察 SSE 流。
6. **验收标准**：
   - SSE 中 `type=worker` 事件 `success: true`
   - 工作区（或沙箱内）可见 `main.py` 与上传的数据文件
   - `/session/workspace-tree` 返回与存储一致的内容

## 测试

单元 / 集成测试（默认不连 live 沙箱）：

```bash
pytest tests/test_sandbox_integration.py
pytest tests/test_runtime_local.py tests/test_workspace_user_isolation.py
```

连接真实 Cube Sandbox 的 live 测试：

```bash
export RUN_CUBE_SANDBOX_INTEGRATION=1
export CUBE_SANDBOX_ENABLED=1
# 并配置 E2B_API_URL、CUBE_TEMPLATE_ID 等
pytest tests/test_sandbox_integration.py -k live
```

## 常见问题

| 现象 | 处理 |
|------|------|
| SDK 502 | 检查 CubeAPI/CubeMaster 是否运行；执行命令前 `unset http_proxy https_proxy` |
| 模板不存在 | 确认 `CUBE_TEMPLATE_ID` 与 `cubemastercli tpl list` 一致 |
| Reader 看不到新文件 | 本地模式直接读工作区；沙箱模式确认 sync 成功或查 workspace-tree |
| Worker 缺包 | 本地模式检查 `RUNNER_PYTHON` 与 `requirements-runner.txt`；沙箱模式检查模板内依赖 |
| 执行超时 | 增大 `RUNTIME_COMMAND_TIMEOUT` 或 Worker 的 `timeout_per_file` |
