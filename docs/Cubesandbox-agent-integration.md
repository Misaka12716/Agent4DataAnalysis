# AgentPlatform 与 Cube Sandbox 集成

本文说明智能体流水线如何通过 **E2B Python SDK** 使用本地部署的 Cube Sandbox，替代原先「宿主机工作区 + subprocess」的执行方式。

## 架构概览

- **每个 session 一个沙箱**：创建会话时 `Sandbox.create(template=...)`，后续上传、Coder 写码、Worker 执行均在同一 MicroVM 内完成。
- **沙箱为真实存储**：上传文件与生成代码通过 `sandbox.files.write()` 写入沙箱；Worker 通过 `sandbox.commands.run("python3 main.py")` 执行。
- **本地目录为镜像**：`tmp/workspaces/<session_id>/` 仍保留，用于 Reader（pandas/Vision）与 `/session/workspace-tree` API；流水线在读取前会从沙箱 **sync** 到本地。
- **分析结束后 pause**：一轮 `run-analysis` 结束后调用 `sandbox.beta_pause()` 释放 VM；下次同一 session 会通过 `Sandbox.connect(sandbox_id)` 恢复。

`sandbox_id` 持久化在 `{workspace}/.cube_sandbox_meta.json`，无需改 MySQL 表结构。

## 环境变量

复制根目录 [`.env.example`](../.env.example) 并按部署情况修改：

| 变量 | 说明 |
|------|------|
| `CUBE_SANDBOX_ENABLED` | `1` 启用沙箱（默认）；`0` 回退本地 subprocess |
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
| [`src/sandbox/session_manager.py`](../src/sandbox/session_manager.py) | create / connect / pause |
| [`src/sandbox/files.py`](../src/sandbox/files.py) | 读写与 sync 到本地镜像 |
| [`src/sandbox/worker.py`](../src/sandbox/worker.py) | 沙箱内执行 Python |
| [`src/utils/workspace_file_ops.py`](../src/utils/workspace_file_ops.py) | Coder 统一读写（沙箱或本地） |
| [`src/worker/workspace_worker.py`](../src/worker/workspace_worker.py) | Worker 统一执行 |

## 关闭沙箱（回退）

开发或单测时可关闭：

```bash
export CUBE_SANDBOX_ENABLED=0
```

行为与改造前一致：文件写入 `tmp/workspaces/`，Worker 使用宿主机 `python3`。

## 常见问题

| 现象 | 处理 |
|------|------|
| SDK 502 | 检查 CubeAPI/CubeMaster 是否运行；执行命令前 `unset http_proxy https_proxy` |
| 模板不存在 | 确认 `CUBE_TEMPLATE_ID` 与 `cubemastercli tpl list` 一致 |
| Reader 看不到新上传文件 | 上传后会自动 sync；也可调用 `/session/workspace-tree` 触发 sync |
| 执行超时 | 增大 `SANDBOX_TIMEOUT` 或 Worker 的 `timeout_per_file` |
