# 启动指令

## 前置条件

1. **MySQL 已就绪**（会话持久化依赖）
2. **默认使用本地 Runtime**（工作区位于 `tmp/workspaces/`）
3. **Python 分工**：
   - 主服务：conda `agentPlatform`（`requirements.txt`）
   - Worker：专用 conda 环境（`requirements-runner.txt`），由 `.env` 的 `RUNNER_PYTHON` 指定

## 安装依赖

```bash
conda create -n agentPlatform python=3.13
conda activate agentPlatform
pip install -r requirements.txt

# Worker 环境（示例）
conda create -n agentPlatform-runner python=3.11
conda activate agentPlatform-runner
pip install -r requirements-runner.txt
```

在仓库根 `.env` 中设置：

```bash
RUNNER_PYTHON=/path/to/agentPlatform-runner/bin/python
```

诊断 Runner：

```bash
bash scripts/diagnose-runner-env.sh
bash scripts/diagnose-runtime.sh
```

## 配置

复制 [`.env.example`](../.env.example) 为 `.env`，配置 LLM 与 MySQL。路径默认相对于仓库根目录（`tmp/` 工作区）。

## 初始化与启动

```bash
bash scripts/init-platform.sh
bash scripts/start.sh
bash scripts/status.sh
bash scripts/stop.sh
```

可选演示数据：

```bash
bash scripts/init-platform.sh --demo
```

健康检查：

```bash
curl http://localhost:52716/health
```

## 演示数据

测试样例位于 `tests/fixtures/table/` 与 `tests/fixtures/text/`。

## 相关文档

- [`BackendAPI.md`](BackendAPI.md)
- [`Models.md`](Models.md)
- [`Tests.md`](Tests.md)
