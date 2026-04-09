# AgentPlatform

一个面向 Excel/CSV 数据分析场景的多智能体平台，提供从任务规划到代码生成、执行与报告输出的完整链路，并通过会话机制支持流式结果与断线恢复。

核心流程：

`Planner -> Coder -> Worker -> Reporter`

---

## 功能概览

- 会话级工作区：每个 `session_id` 对应独立工作目录。
- 文件上传：支持将 `xlsx/xls/csv` 上传到会话工作区。
- 任务流式分析：后端通过 SSE 持续推送分析阶段事件和报告片段。
- 断线重连：会话内容按版本写入 MySQL，可通过快照接口恢复。
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
│   ├── backend/                # FastAPI 服务与路由
│   ├── frontend/               # Streamlit 测试前端
│   ├── planner/                # 规划器（LangGraph）
│   ├── coder/                  # 代码生成
│   ├── worker/                 # 代码执行
│   ├── reporter/               # 报告生成（流式）
│   ├── db/                     # 会话与数据库模型
│   ├── utils/                  # 配置、MySQL、工作区管理等工具
│   └── configs/                # 提示词配置
├── requirements.txt
└── README.md
```

---

## 运行环境

- Python: `3.13.x`（项目文档示例为 `3.13.7`）
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
- MySQL：
  - `MYSQL_HOST`（支持环境变量）
  - `MYSQL_PORT`（支持环境变量）
  - `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB`
- 工作区路径：
  - `TEMP_FOLDER`（会话工作区位于 `TEMP_FOLDER/workspaces`）

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
  - `file`: 文件
  - `session_id`: 会话 ID（必填）
  - `user_id`: 用户 ID（默认 0）

### `GET /session/snapshot`

获取会话累计内容与当前版本号（用于断线重连）。

- query:
  - `session_id`

### `POST /run-analysis`

执行流式分析（SSE），触发完整链路：

`Planner -> Coder -> Worker -> Reporter`

- json:
  - `session_id`
  - `input_data`

---

## 工作机制（简述）

1. 前端上传数据文件到会话工作区。
2. Planner 基于需求和工作区数据结构生成规划。
3. Coder 依据规划和文件上下文生成 Python 代码并写入工作区（默认 `main.py`）。
4. Worker 在工作区执行代码，收集 `stdout/stderr`。
5. Reporter 结合规划与执行结果，流式产出分析报告。
6. 每个阶段事件写入会话内容表，支持快照恢复。

---

## 常见问题

### 1) 后端启动时报 MySQL 连接失败

- 检查 MySQL 是否启动。
- 检查 `src/utils/config.py` 中 MySQL 配置是否正确。
- 确认数据库和表已创建。

### 2) 流式分析无输出或报错

- 检查模型服务地址 `OPENAI_COMPATIBLE_API_BASE` 是否可访问。
- 检查模型名是否在本地可用。
- 检查上传文件是否成功进入会话工作区。

### 3) 上传后找不到文件

系统会将文件重命名为 `data.xxx`、`data_1.xxx` 等统一命名，请在返回值中的 `relative_path` 查看实际文件名。

---

## 相关文档

- 启动说明：`docs/StartInstruction.md`
- Planner 说明：`docs/Planner.md`
- Neo4j 指南（可选）：`docs/Neo4j.md`
