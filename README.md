# AgentPlatform

面向 Excel/CSV 等表格数据分析的多智能体平台，提供从任务规划到代码生成、执行与报告输出的完整链路，并通过会话机制支持流式结果与断线恢复。

**顶层编排**使用 **LangGraph + LangChain 结构化 Supervisor** 包裹 `Planner / Coder / Worker / Reporter`。Supervisor 根据当前状态决定下一步子阶段，并在 Worker 失败时优先回到 Coder 做修正。

典型 happy path：

`Planner → Coder → Worker → Reporter`

---

## 功能概览

- **会话级工作区**：每个用户在 `tmp/workspaces/<user_id>/<project_id>/sessions/<session_id>/` 拥有独立目录
- **项目模型**：Project 是 RBAC 与资产容器；Session 是一次分析/对话的执行单元
- **代码执行**：Worker 通过 `RUNNER_PYTHON` 使用专用 conda 环境（依赖见 [`requirements-runner.txt`](requirements-runner.txt)），与 FastAPI 主环境隔离
- **文件上传**：支持表格 / 文本 / 文档 / 图片 / 医学影像等多种格式
- **流式分析**：后端通过 SSE 推送编排决策、各阶段事件与报告片段
- **断线重连**：可通过快照接口拉取当前累计内容与版本号

---

## 项目结构

```text
AgentPlatform/
├── docs/                   # API 与运维文档
├── scripts/                # 启动、初始化、诊断脚本
├── web/                    # Vue 3 前端（Vite + TypeScript）
├── src/
│   ├── backend/            # FastAPI 服务与路由
│   ├── orchestrator/       # LangGraph 顶层编排
│   ├── planner/            # 规划器
│   ├── coder/              # 代码生成
│   ├── worker/             # 工作区代码执行
│   ├── runtime/            # 统一执行层（本地默认；可选沙箱）
│   ├── sandbox/            # Cube Sandbox 可选后端
│   ├── reporter/           # 报告生成
│   ├── reader/             # 多格式文件读取
│   ├── db/                 # 会话、项目、RBAC
│   ├── utils/              # 工具库
│   └── configs/            # 配置与提示词
├── tests/                  # pytest 测试
├── requirements.txt        # 主服务依赖
├── requirements-runner.txt # Worker 执行环境依赖
└── README.md
```

---

## 快速开始

### 1. 安装依赖

```bash
conda create -n agentPlatform python=3.13
conda activate agentPlatform
pip install -r requirements.txt

# Worker 环境（另建 conda 环境后安装）
pip install -r requirements-runner.txt
```

### 2. 配置

复制 [`.env.example`](.env.example) 为 `.env`，配置 LLM 接口与 MySQL：

```bash
cp .env.example .env
```

关键项见 [`src/configs/config.py`](src/configs/config.py) 与 [`docs/Models.md`](docs/Models.md)。

### 3. 初始化与启动

```bash
bash scripts/init-platform.sh
bash scripts/start.sh
curl http://localhost:52716/health
```

可选演示数据：

```bash
bash scripts/init-platform.sh --demo
```

### 4. 启动前端（开发）

```bash
cd web
npm install
npm run dev
```

浏览器访问 `http://localhost:5173`（Vite 代理 API 到 `52716`）。

生产构建后，若存在 `web/dist/`，后端会自动托管静态资源：

```bash
cd web && npm run build
bash scripts/start.sh
# 访问 http://localhost:52716/
```

详见 [`docs/Frontend.md`](docs/Frontend.md)。

---

## 核心 API

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `POST /session/create` | 创建会话 |
| `POST /session/upload-excel` | 上传文件到工作区 |
| `GET /session/snapshot` | 断线重连快照 |
| `POST /run-analysis` | 流式分析（SSE） |

完整说明见 [`docs/BackendAPI.md`](docs/BackendAPI.md)、[`docs/SSE_Details.md`](docs/SSE_Details.md)。

---

## 工作机制

1. 创建会话并上传数据到工作区
2. 调用 `POST /run-analysis` 触发 Supervisor 编排
3. Planner 产出分析规划；Coder 生成/修正 Python 代码；Worker 在隔离环境执行；Reporter 流式输出报告
4. 每个 SSE 片段先写入 MySQL，再推送给客户端

---

## 测试

```bash
pytest tests/agent tests/runtime tests/reader tests/upload tests/project
```

详见 [`docs/Tests.md`](docs/Tests.md)。

---

## 相关文档

- [`docs/Frontend.md`](docs/Frontend.md) — Web 前端开发与部署
- [`docs/StartInstruction.md`](docs/StartInstruction.md) — 启动说明
- [`docs/Models.md`](docs/Models.md) — 大模型配置
- [`docs/MySQL.md`](docs/MySQL.md) — 数据库说明

---

## License

MIT — 见 [LICENSE](LICENSE)
