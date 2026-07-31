# AgentPlatform

一个面向 Excel/CSV 数据分析场景的多智能体平台，提供从任务规划到代码生成、执行与报告输出的完整链路，并通过会话机制支持流式结果与断线恢复。

**顶层编排**（与「固定线性流水线」不同）：使用 **LangGraph + LangChain 结构化 Supervisor** 包裹 `Planner / Coder / Worker / Reporter`。Supervisor 根据当前状态决定下一步子阶段，并在 Worker 失败时优先回到 Coder 做修正（可多次），同时带有 Planner 重试与 Supervisor 调用次数上限，避免无限循环。

典型 happy path 仍为：

`Planner → Coder → Worker → Reporter`

实际运行中会在 **Supervisor ↔ 各子阶段** 之间多次往返，直到结束或触发上限。

---

## 功能概览

- 会话级工作区：每个用户在 `TEMP_FOLDER/workspaces/<user_id>/<project_id>/sessions/<session_id>/` 拥有独立目录（历史数据可能仍在 `.../<user_id>/<session_id>/`，只读兼容）；默认经 **本地 Runtime**（[`src/runtime/`](src/runtime/)）读写与执行 Python。
- **项目模型**：Project 是 RBAC 与资产容器；Session 是一次分析/对话的执行单元。分析前须创建会话并将数据上传到会话工作区（或从项目 `raw/` 复制，见 `POST /session/copy-from-project-raw`）。
- 代码执行：Worker 通过 **`RUNNER_PYTHON`** 使用专用 conda 环境 `agentPlatform-runner`（依赖见 [`requirements-runner.txt`](requirements-runner.txt)），与 FastAPI 主环境隔离。conda `base` 为根环境不可改名，勿当作 Runner。
- 文件上传：支持将 `xlsx/xls/csv` 等写入工作区根目录；无同名冲突时保留用户原名，冲突时自动改为 `原名 (1).ext`。
- 任务流式分析：后端通过 SSE 持续推送编排决策、各阶段事件与报告片段；每条有效负载会先写入 MySQL 会话内容（累计全文 + 版本号），再推送给客户端。
- 断线重连：可通过快照接口拉取当前累计内容与版本号。
- 最简测试前端：内置 Streamlit 页面用于联调上传/快照/流式分析接口。

---

## 项目结构

```text
AgentPlatform/
├── docs/
│   ├── StartInstruction.md     # 启动说明
│   ├── Planner.md              # Planner 模块说明
│   └── Neo4j.md                # Neo4j 备份/恢复说明（可选）
├── src/
│   ├── backend/                # FastAPI 服务与路由（含 SSE 流式入口）
│   ├── frontend/               # Streamlit 测试前端
│   ├── orchestrator/           # LangGraph 顶层编排（Supervisor + 子图节点）
│   ├── planner/                # 规划器（含工作区上下文）
│   ├── coder/                  # 代码生成与失败修正写入
│   ├── worker/                 # 工作区代码执行（经 runtime）
│   ├── runtime/                # 统一执行层（本地默认；可选沙箱适配）
│   ├── sandbox/                # Cube Sandbox 可选后端（供 runtime 适配器）
│   ├── reporter/               # 报告生成（流式 chunk）
│   ├── db/                     # 会话与数据库模型
│   ├── utils/                  # MySQL、工作区管理与文件操作等工具
│   └── configs/                # 提示词等配置
├── requirements.txt
├── requirements-runner.txt       # Runner 执行环境依赖（数据分析包）
└── README.md
```

---

## 运行环境

- **主服务 Python**：`3.13.x`（conda 环境 `agentPlatform`，`/opt/miniconda/envs/agentPlatform`，见 [`requirements.txt`](requirements.txt)）
- **Runner Python**：conda `agentPlatform-runner`（本机 `/data/pjw/.conda/envs/agentPlatform-runner`；依赖 [`requirements-runner.txt`](requirements-runner.txt)）。由 `.env` 的 `RUNNER_PYTHON` 指定。**base 不可改名，勿用作专用 Runner。**
- 数据库: MySQL（用于会话内容和工作区路径持久化）
- 推荐 OS: Linux

---

## 安装依赖

可以使用 Conda（与项目现有文档一致）：

```bash
conda create -n agentPlatform python=3.13.7
conda activate agentPlatform
pip install -r requirements.txt
# 含文档/表格解析：python-docx、pypdf/PyPDF2、pydicom、openpyxl、xlrd、Pillow 等

# Worker：专用 agentPlatform-runner（本机路径见上）
# bash scripts/setup-runner-env.sh
# 或按 requirements-runner.txt 安装到已有 agentPlatform-runner
# .env: RUNNER_PYTHON=/data/pjw/.conda/envs/agentPlatform-runner/bin/python
bash scripts/diagnose-runner-env.sh
```

可选：注册 Jupyter 内核

```bash
python -m ipykernel install --user --name agentPlatform --display-name "Python (agentPlatform)"
```

---

## 配置说明

主要配置位于 [`src/configs/config.py`](src/configs/config.py)。

关键项（按需修改）：

- 模型接口（三模型分工，详见 [`docs/Models.md`](docs/Models.md)）：
  - `OPENAI_COMPATIBLE_API_BASE`（默认 `http://localhost:11434/v1`）
  - `API_KEY`
  - `DEFAULT_MODEL`：通用文本（Planner / Reader / Reporter / 临床等，默认 `glm-4.7-flash:q4_K_M`）
  - `DEFAULT_CODER_MODEL`：Coder 专用（默认 `qwen3-coder:30b`）
  - `DEFAULT_VISION_MODEL`：OCR / 图片识别（默认 `deepseek-ocr:latest`）
  - `DEFAULT_ORCHESTRATOR_MODEL`：Supervisor 路由用模型（可通过环境变量覆盖，默认与 `DEFAULT_MODEL` 相同）
- 编排上限（可通过环境变量覆盖）：
  - `MAX_SUPERVISOR_INVOCATIONS`：Supervisor 决策次数上限（默认 `24`）；临近上限时会强制进入 Reporter，避免卡死
  - `MAX_CODER_CORRECTIONS`：Worker 失败后 Coder 修正次数上限（默认 `5`）
  - `MAX_PLANNER_RETRIES`：Planner 重试上限（默认 `4`）
- 分析输出语言：`LANGUAGE`（如 `zh` / `en`），传入编排与 Reporter
- MySQL：
  - `MYSQL_HOST`（支持环境变量）
  - `MYSQL_PORT`（支持环境变量）
  - `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB`
- 工作区与临时路径：
  - `TEMP_FOLDER`：项目工作区位于 `TEMP_FOLDER/workspaces/<user_id>/<project_id>/`；会话位于其下 `sessions/<session_id>/`
  - 历史 legacy 布局 `workspaces/<user_id>/<session_id>/` 仍可读；迁移见 `scripts/migrate-legacy-sessions.sh`
  - 仓库内 `PATH` 等路径为开发机示例，部署到新环境时请改为本机实际路径
- **执行 Runtime**（默认本地，见 [`src/runtime/config.py`](src/runtime/config.py)）：

| 环境变量 | 说明 | 默认 |
|----------|------|------|
| `RUNNER_PYTHON` | Worker 解释器绝对路径（专用 `agentPlatform-runner`） | 本机示例：`/data/pjw/.conda/envs/agentPlatform-runner/bin/python` |
| `CUBE_SANDBOX_ENABLED` | `1` 启用 Cube Sandbox 后端；`0` 本地 Runtime | `0` |
| `RUNTIME_COMMAND_TIMEOUT` | 单次命令超时（秒） | `300` |
| `RUNTIME_MAX_OUTPUT_CHARS` | stdout/stderr 截断上限 | `524288` |

- **Cube Sandbox（可选）**：`CUBE_SANDBOX_ENABLED=1` 时需配置 Cube API 与模板（见 [`.env.example`](.env.example)）：

| 环境变量 | 说明 | 示例 |
|----------|------|------|
| `E2B_API_URL` | Cube API 地址 | `http://127.0.0.1:3000` |
| `E2B_API_KEY` | API Key | `e2b_000000` |
| `CUBE_TEMPLATE_ID` | 沙箱模板 ID | `tpl-78c1861fc2b54381947d33e2` |
| `SANDBOX_WORKDIR` | 沙箱内工作目录 | `/home/user` |
| `SANDBOX_TIMEOUT` | 沙箱命令超时（秒） | `600` |

> 注意：当前仓库中的默认数据库账号密码仅适用于本地开发示例，生产环境请务必改为安全配置。

---

## 数据库初始化

系统依赖以下表：

- `session_user`
- `session_content`

建表 SQL 可参考 [`src/db/models.py`](src/db/models.py) 中的：

- `SESSION_USER_TABLE_DDL`
- `SESSION_CONTENT_TABLE_DDL`

如果你需要直接手工初始化，可使用如下示例（与你的数据库名保持一致）：

```sql
CREATE TABLE IF NOT EXISTS session_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL UNIQUE COMMENT '会话ID',
    user_id BIGINT NOT NULL COMMENT '用户ID',
    workspace_abs_path VARCHAR(512) NOT NULL COMMENT '工作区绝对路径',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '会话-用户关联表';

CREATE TABLE IF NOT EXISTS session_content (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL COMMENT '会话ID',
    version INT NOT NULL DEFAULT 0 COMMENT '版本号/片段序号',
    content LONGTEXT NOT NULL COMMENT '完整累计内容',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_session_version (session_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '会话内容存储表';
```

---

## 启动方式

**前置**：MySQL 已就绪；默认本地 Runtime 无需 Cube。完整步骤见 [`docs/StartInstruction.md`](docs/StartInstruction.md)。启用 Cube Sandbox 时见 [`docs/Cubesandbox-deploy.md`](docs/Cubesandbox-deploy.md)。

在**仓库根目录**执行：

```bash
bash scripts/init-platform.sh             # 首次：建表 + 导入模板 + 生成 fixtures
bash scripts/start.sh                     # 仅后端 52716
bash scripts/start.sh --with-frontend     # 后端 + 联调前端 8501
bash scripts/status.sh                    # 查看状态
bash scripts/stop.sh --all                # 停止全部
```

- **后端**：`http://<主机>:52716`
- **联调前端**：`http://<主机>:8501`（`frontend.py` 多页：流式分析 / 模板分析 / 项目成员；**仅调试用**，正式前端按 [`docs/TemplateAPI.md`](docs/TemplateAPI.md) 对接 HTTP）
- **验收登录**（可选）：`ACCEPTANCE_MODE=1 bash scripts/init-platform.sh --acceptance` 后，侧栏 `13800000000` / `888888`
- **演示数据**：运行 `init-platform.sh` 后位于 `tests/fixtures/`（横截面 + 纵向样本）

健康检查：

```bash
curl http://localhost:52716/health
```

返回示例字段：`status`、`service`（`agent-workflow-server`）、`version`（当前为 `1.1`）。

完整说明见 [`docs/StartInstruction.md`](docs/StartInstruction.md)。鉴权见 [docs/AUTH.md](docs/AUTH.md)。

---

## API 简要说明

除 `/health`、`/auth/send-sms-code`、`/auth/login-with-sms` 外，接口需携带 `Authorization: Bearer <access_token>`。详见 [docs/AUTH.md](docs/AUTH.md) 与 [docs/BackendAPI.md](docs/BackendAPI.md)。

### `GET /health`

服务健康检查。

### `POST /session/upload-excel`

上传表格 / 图片 / 文本到会话工作区根目录（扩展名白名单与 Reader 一致；路径名保留历史兼容）。

- form-data:
  - `file`: 文件（table: xlsx/xls/csv/tsv；image: png/jpg/jpeg/gif/webp/bmp；text: txt/md/json/yaml/yml/log/xml/html/htm）
  - `session_id`: 会话 ID（必填）

- 成功响应额外字段：`original_filename`、`file_category`（`table` / `image` / `text`）
- 不支持类型返回 `415`

### `GET /session/snapshot`

获取会话累计内容与当前版本号（用于断线重连）。

- query:
  - `session_id`

### `POST /run-analysis`

执行流式分析（SSE），触发 **编排后的** 完整链路（Supervisor 调度下的 Planner / Coder / Worker / Reporter）。

- json:
  - `session_id`
  - `input_data`

---

## 工作机制（与代码一致）

1. **创建会话**：`POST /session/create` 在指定项目下初始化 `tmp/workspaces/<user_id>/<project_id>/sessions/<session_id>/`，并绑定本地 Runtime（`ensure_runtime`）。
2. **上传**：文件经 `runtime.files.write` 写入**会话工作区**（`POST /session/upload-excel`）；项目 `raw/` 预置文件需 `POST /session/copy-from-project-raw` 复制到会话后再分析。
3. **Supervisor**：每次子阶段结束后回到 Supervisor，由结构化 LLM 决策下一步（`planner` / `coder` / `worker` / `reporter` / `finish`）；非法跳步会被代码侧「钳制」为合法路由。
4. **Planner**：产出包含「需求解析」「步骤分解」等字段的规划；无效规划会触发重试直至上限。
5. **Coder**：首次生成并写入工作区代码（默认 `main.py`）；Worker 失败且未超修正次数时走「修正写入」路径，并附带 stderr 等错误摘要。
6. **Worker**：经 `RUNNER_PYTHON` 指定的解释器执行 `runtime.commands.run("python3 <相对路径>")`，汇总 `stdout/stderr` 与成功标志。
7. **Reader / workspace-tree**：直接读取工作区目录。
8. **Reporter**：根据规划摘要与 Worker 结果流式输出报告片段。
9. **流水线结束**：调用 `release_runtime(session_id)`（沙箱模式下 pause VM）；SSE 持久化逻辑不变——每个 JSON 片段先写入 MySQL，再推送客户端。

### SSE 中常见的 `type` 字段（便于联调）

| `type` | 含义 |
|--------|------|
| `orchestrator` | Supervisor 决策：下一步路由、理由、反馈等 |
| `planner` | Planner 子流事件（如阶段进度、规划结果） |
| `coder` | 代码生成/修正结果列表 |
| `worker` | 执行结果汇总 |
| `report_chunk` | 报告正文增量 |
| `streaming_ended` | 整次流式任务正常结束 |
| `streaming_error` / `error` | 运行期或流水线未正常完成时的错误提示 |

---

## 常见问题

### 1) 后端启动时报 MySQL 连接失败

- 检查 MySQL 是否启动。
- 检查 [`src/configs/config.py`](src/configs/config.py) 中 MySQL 配置是否正确。
- 确认数据库和表已创建。

### 2) 流式分析无输出或报错

- 检查模型服务地址 `OPENAI_COMPATIBLE_API_BASE` 是否可访问。
- 检查各阶段模型名（含 `DEFAULT_ORCHESTRATOR_MODEL`）在本地是否可用。
- 检查上传文件是否成功进入会话工作区。

### 3) 上传后找不到文件

无冲突时保留用户原始文件名；若工作区已存在同名文件，则自动重命名为 `原名 (1).ext`、`原名 (2).ext` …。请以返回值中的 `relative_path` 为准查看实际存盘名，`original_filename` 为上传时的客户端原名。会话工作区位于 `tmp/workspaces/<user_id>/<project_id>/sessions/<session_id>/`。

### 4) Worker 报错或缺少 pandas 等包

- 确认 `.env` 中 `RUNNER_PYTHON=/data/pjw/.conda/envs/agentPlatform-runner/bin/python`，并运行 `bash scripts/diagnose-runner-env.sh`。
- 缺包：`/data/pjw/.conda/envs/agentPlatform-runner/bin/python -m pip install -r requirements-runner.txt`。
- 重建：`bash scripts/setup-runner-env.sh`（或按 StartInstruction 用 `-p` 在 `/data/pjw/.conda/envs/` 创建）。
- 启用 Cube Sandbox 时另需检查模板内 Python 与依赖。

### 5) 流水线未走到报告或频繁回溯

- 查看 SSE 中 `type=orchestrator` 的路由与 `reason`，确认是规划无效、代码未写入还是 Worker 报错。
- 适当调大或检查环境变量中的 `MAX_*` 上限；仍失败时请结合 Worker 的 stderr 与模型能力排查。

### 6) Cube API 不可达或沙箱/template 配置错误（仅 CUBE_SANDBOX_ENABLED=1）

- 确认 Cube Sandbox 控制面已启动：`curl --noproxy '*' http://127.0.0.1:3000/health`。
- 检查 `E2B_API_URL`、`E2B_API_KEY`、`CUBE_TEMPLATE_ID` 是否与部署一致。
- 启动后端前 `unset http_proxy https_proxy`，避免 SDK 经代理返回 502。
- 本地联调默认 `CUBE_SANDBOX_ENABLED=0`；Cube 不可用时 Runtime 工厂会自动降级本地。

---

## 相关文档

- **正式前端对接（产品 UI）**：[`docs/2.1.1FrontendIntegrationGuide.md`](docs/2.1.1FrontendIntegrationGuide.md)
- 测试说明：[`docs/Tests.md`](docs/Tests.md)
- 启动说明：[`docs/StartInstruction.md`](docs/StartInstruction.md)
- 模板分析 API：[`docs/TemplateAPI.md`](docs/TemplateAPI.md)
- Cube Sandbox 部署：[`docs/Cubesandbox-deploy.md`](docs/Cubesandbox-deploy.md)
- AgentPlatform 沙箱集成：[`docs/Cubesandbox-agent-integration.md`](docs/Cubesandbox-agent-integration.md)
- Cube Sandbox 使用说明：[`docs/Cubesandbox-using.md`](docs/Cubesandbox-using.md)
- 大模型部署与配置：[`docs/Models.md`](docs/Models.md)
- 后端接口说明：[`docs/BackendAPI.md`](docs/BackendAPI.md)
- SSE 详细说明：[`docs/SSE_Details.md`](docs/SSE_Details.md)
- MySQL 说明：[`docs/MySQL.md`](docs/MySQL.md)
