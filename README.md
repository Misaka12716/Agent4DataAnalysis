# AgentPlatform

一个面向 Excel/CSV 数据分析场景的多智能体平台，提供从任务规划到代码生成、执行与报告输出的完整链路，并通过会话机制支持流式结果与断线恢复。

**顶层编排**（与「固定线性流水线」不同）：使用 **LangGraph + LangChain 结构化 Supervisor** 包裹 `Planner / Coder / Worker / Reporter`。Supervisor 根据当前状态决定下一步子阶段，并在 Worker 失败时优先回到 Coder 做修正（可多次），同时带有 Planner 重试与 Supervisor 调用次数上限，避免无限循环。

典型 happy path 仍为：

`Planner → Coder → Worker → Reporter`

实际运行中会在 **Supervisor ↔ 各子阶段** 之间多次往返，直到结束或触发上限。

---

## 功能概览

- 会话级工作区：每个 `session_id` 对应独立工作目录（位于 `TEMP_FOLDER/workspaces/<session_id>`）。
- 文件上传：支持将 `xlsx/xls/csv` 上传到会话工作区根目录，并按 `data.xxx`、`data_1.xxx` 规则命名。
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
│   ├── worker/                 # 工作区内代码执行
│   ├── reporter/               # 报告生成（流式 chunk）
│   ├── db/                     # 会话与数据库模型
│   ├── utils/                  # 配置、MySQL、工作区管理等工具
│   └── configs/                # 提示词配置
├── requirements.txt
└── README.md
```

---

## 运行环境

- Python: `3.13.x`（项目文档示例为 `3.13.7`）
- 依赖栈含 **LangChain / LangGraph**（见 `requirements.txt`）
- 数据库: MySQL（用于会话内容和工作区路径持久化）
- 推荐 OS: Linux

---

## 安装依赖

可以使用 Conda（与项目现有文档一致）：

```bash
conda create -n agentPlatform python=3.13.7
conda activate agentPlatform
pip install -r requirements.txt
```

可选：注册 Jupyter 内核

```bash
python -m ipykernel install --user --name agentPlatform --display-name "Python (agentPlatform)"
```

---

## 配置说明

主要配置位于 `src/utils/config.py`。

关键项（按需修改）：

- 模型接口：
  - `OPENAI_COMPATIBLE_API_BASE`（默认 `http://localhost:11434/v1`）
  - `API_KEY`
  - `DEFAULT_MODEL` / `DEFAULT_CODER_MODEL`
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
  - `TEMP_FOLDER`：会话工作区位于 `TEMP_FOLDER/workspaces`
  - 仓库内 `PATH` 等路径为开发机示例，部署到新环境时请改为本机实际路径

> 注意：当前仓库中的默认数据库账号密码仅适用于本地开发示例，生产环境请务必改为安全配置。

---

## 数据库初始化

系统依赖以下表：

- `session_user`
- `session_content`

建表 SQL 可参考 `src/db/models.py` 中的：

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

### 1) 启动后端（FastAPI）

在项目根目录执行：

```bash
cd src
uvicorn backend.server:app --host 0.0.0.0 --port 52716
```

健康检查：

```bash
curl http://localhost:52716/health
```

返回示例字段：`status`、`service`（`agent-workflow-server`）、`version`（当前为 `1.1`）。

### 2) 启动前端（Streamlit）

另开一个终端，在项目根目录执行：

```bash
cd src
streamlit run frontend/frontend.py
```

前端默认访问后端地址：`http://localhost:52716`

---

## API 简要说明

### `GET /health`

服务健康检查。

### `POST /session/upload-excel`

上传 Excel/CSV 到会话工作区根目录。

- form-data:
  - `file`: 文件（服务端有大小上限，与路由中配置一致）
  - `session_id`: 会话 ID（必填）
  - `user_id`: 用户 ID（默认 0）

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

1. **上传**：前端将数据文件写入会话工作区；Planner 侧会列举工作区文件并读取 Excel 结构样例，作为规划上下文。
2. **Supervisor**：每次子阶段结束后回到 Supervisor，由结构化 LLM 决策下一步（`planner` / `coder` / `worker` / `reporter` / `finish`）；非法跳步会被代码侧「钳制」为合法路由（例如无有效规划时不能进 Coder，未执行 Worker 时不能进 Reporter）。
3. **Planner**：产出包含「需求解析」「步骤分解」等字段的规划；无效规划会触发重试直至上限。
4. **Coder**：首次生成并写入工作区代码（默认 `main.py`）；若上一轮 Worker 失败且未超修正次数，则走「修正写入」路径，并附带 stderr 等错误摘要。
5. **Worker**：在工作区按配置的执行模式运行指定脚本，汇总各文件 `stdout/stderr` 与成功标志。
6. **Reporter**：根据规划摘要与 Worker 结果流式输出报告片段。
7. **持久化与 SSE**：`streaming_task_generator` 在消费编排流时，将每个 JSON 片段先 `append` 到会话内容表，再作为 SSE `data:` 行推送；正常结束会追加 `streaming_ended`；异常为 `streaming_error` 等。

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
- 检查 `src/utils/config.py` 中 MySQL 配置是否正确。
- 确认数据库和表已创建。

### 2) 流式分析无输出或报错

- 检查模型服务地址 `OPENAI_COMPATIBLE_API_BASE` 是否可访问。
- 检查各阶段模型名（含 `DEFAULT_ORCHESTRATOR_MODEL`）在本地是否可用。
- 检查上传文件是否成功进入会话工作区。

### 3) 上传后找不到文件

系统会将文件重命名为 `data.xxx`、`data_1.xxx` 等统一命名，请在返回值中的 `relative_path` 查看实际文件名。

### 4) 流水线未走到报告或频繁回溯

- 查看 SSE 中 `type=orchestrator` 的路由与 `reason`，确认是规划无效、代码未写入还是 Worker 报错。
- 适当调大或检查环境变量中的 `MAX_*` 上限；仍失败时请结合 Worker 的 stderr 与模型能力排查。

---

## 相关文档

- 启动说明：`docs/StartInstruction.md`
- Planner 说明：`docs/Planner.md`
- Neo4j 指南（可选）：`docs/Neo4j.md`
