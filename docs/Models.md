# 大模型部署与配置说明

本文档汇总 AgentPlatform 所用大模型的**部署方式**、**模型清单**、**分工策略**与**平台内配置映射**，便于运维与开发统一查阅。

---

## 1. 架构概览

平台通过 **OpenAI 兼容 HTTP API** 调用本地模型，不直接在应用进程内加载权重。调用链如下：

```text
AgentPlatform (LangChain ChatOpenAI)
    → OPENAI_API_BASE (/v1)  # 兼容别名 OPENAI_COMPATIBLE_API_BASE
    → Docker 内 Ollama（容器 medical_ollama）
    → 各模型权重（pull 到容器内）
```

- **网关地址**（当前开发配置）：`http://192.168.4.110:12716/v1`
- **协议**：与 OpenAI Chat Completions 兼容（`POST /v1/chat/completions`、可选 `GET /v1/models`）
- **指纹**：响应中可见 `system_fingerprint: fp_ollama`，表明后端为 Ollama 兼容服务
- **应用侧配置**：仓库根 [`.env`](../.env)（权威）；[`src/configs/config.py`](../src/configs/config.py) 转发为模块常量

---

## 2. Docker 部署

### 2.1 容器信息

| 项 | 值 |
|---|---|
| 容器名 | `medical_ollama` |
| 运行时 | Docker + Ollama |
| 对外 API | 由宿主机端口映射到容器内 Ollama（当前映射至 `192.168.4.110:12716`） |

### 2.2 常用运维命令

进入容器 Shell（原文档保留命令）：

```bash
sudo docker exec -it medical_ollama /bin/bash
```

容器内查看已拉取模型：

```bash
ollama list
```

拉取 / 更新模型（在容器内或宿主机 `ollama` CLI，视部署方式而定）：

```bash
# 通用文本（Planner / Supervisor / Reader / Reporter / 临床等）
ollama pull glm-4.7-flash:q4_K_M

# Supervisor / Orchestrator 路由
ollama pull qwen2.5:7b

# 代码生成 / 失败修正
ollama pull qwen3-coder:30b

# OCR / 图片文字识别
ollama pull deepseek-ocr:latest
```

重启容器（示例，按实际 compose / run 命令调整）：

```bash
sudo docker restart medical_ollama
```

### 2.3 健康检查

列出可用模型：

```bash
curl -s http://192.168.4.110:12716/v1/models | jq .
```

冒烟对话（替换 `model` 为实际名称）：

```bash
curl -s http://192.168.4.110:12716/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "glm-4.7-flash:q4_K_M",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 16
  }'
```

**当前 API 已注册模型**（`GET /v1/models`，以线上为准）：

| 模型 ID | 状态 | 用途 |
|---|---|---|
| `glm-4.7-flash:q4_K_M` | 已部署 | 通用文本（长期推荐；当前临时未用） |
| `qwen3-coder:30b` | 已部署 | Coder 专用（长期推荐；当前临时未用） |
| `deepseek-ocr:latest` | 已部署 | **OCR / 图片文字识别**（Reader Vision） |
| `qwen2.5:7b` | 已部署 | **临时统一文本模型**（Planner / Supervisor / Coder / Reader / Reporter / 临床） |
| `qwen3.6:27b` | 已部署 | 多模态备用 |
| `qwen2.5:14b` | 已部署 | 轻量备用 |

---

## 3. 模型分工策略

采用**三模型**策略（长期推荐）：通用文本用较轻量的 `glm-4.7-flash:q4_K_M`；代码生成/修正用 `qwen3-coder:30b`；图片用 `deepseek-ocr:latest`。

**当前临时部署**：除 OCR / 多模态外，文本角色统一为 `qwen2.5:7b`（见 `.env`）。

| 角色 | 推荐模型 | 说明 |
|---|---|---|
| **Coder**（代码生成、失败修正） | `qwen2.5:7b`（临时） | `DEFAULT_CODER_MODEL`；长期推荐 `qwen3-coder:30b` |
| **Planner**（需求解析、步骤分解） | `qwen2.5:7b`（临时） | `DEFAULT_MODEL`；长期推荐 `glm-4.7-flash:q4_K_M` |
| **Orchestrator / Supervisor**（路由决策） | `qwen2.5:7b` | `DEFAULT_ORCHESTRATOR_MODEL` |
| **Reader**（工作区摘要压缩） | `qwen2.5:7b`（临时） | `DEFAULT_READER_MODEL` |
| **Reader Vision**（OCR / 图片文字识别） | `deepseek-ocr:latest` | `DEFAULT_VISION_MODEL`（不变） |
| **Reporter**（最终报告） | `qwen2.5:7b`（临时） | `DEFAULT_MODEL` |
| **Reader 表头 LLM**（可选） | `qwen2.5:7b`（临时） | 仅当 `READER_ENABLE_LLM_TABLE_HEADER=1` |
| **临床映射 / 临床报告** | `qwen2.5:7b`（临时） | `LLM_MODEL` / `CLINICAL_REPORT_MODEL` |
| **Worker 执行** | 不调用 LLM | Cube Sandbox 内执行用户代码；与 Coder 模型无关，但依赖沙箱模板中的 Python 环境（见 [Cubesandbox-agent-integration.md](./Cubesandbox-agent-integration.md)） |

### 3.1 代码中的调用点

| 模块 | 文件 | 配置项 |
|---|---|---|
| Planner | `src/planner/planner_utils.py` | `DEFAULT_MODEL` |
| Coder | `src/coder/workspace_coder.py` | `DEFAULT_CODER_MODEL` |
| Orchestrator Supervisor | `src/orchestrator/analysis_pipeline_graph.py` | `DEFAULT_ORCHESTRATOR_MODEL` |
| Reader 摘要 | `src/reader/nodes/synthesize.py` | `DEFAULT_READER_MODEL` |
| Reader 图片 | `src/reader/handlers/image.py` | `DEFAULT_VISION_MODEL` |
| Reader 表头 LLM | `src/reader/handlers/table.py` | `DEFAULT_MODEL` |
| Reporter | `src/reporter/report_agent.py` | `DEFAULT_MODEL` |
| Worker 执行 | `src/worker/workspace_worker.py` | 不调用 LLM；Cube Sandbox 内执行用户代码（`sandbox.commands.run`），依赖沙箱模板中的 Python 环境，见 [Cubesandbox-agent-integration.md](./Cubesandbox-agent-integration.md) |

### 3.2 Supervisor 与 json_schema 兼容性

多数本地 / OpenAI 兼容网关（含默认的 `glm-4.7-flash`、`qwen` 等）**不支持** `response_format=json_schema`，会返回 500（如 `failed to load model vocabulary required for format`）。

编排层采用**白名单**：仅当模型名以 `gpt-` / `o1-` / `o3-` / `o4-` / `chatgpt-` 开头时才尝试 json_schema；其它模型（含默认 Orchestrator）只走 `function_calling` 与原始 JSON 解析回退（见 `analysis_pipeline_graph.py` 中 `_should_try_json_schema_method`）。

---

## 4. 平台配置（`.env` 为准，`config.py` 转发）

运行时权威来源为仓库根目录 **`.env`**（或启动前 `export`）。[`src/configs/config.py`](../src/configs/config.py) 在 import 时幂等 `load_dotenv`，再通过 `os.getenv` 导出常量供业务 import。

### 4.1 代码侧默认值（无 env 时的回退）

```python
GENERAL_MODEL = "glm-4.7-flash:q4_K_M"   # 通用文本默认
CODER_MODEL = "qwen3-coder:30b"     # Coder 专用
VISION_MODEL = "deepseek-ocr:latest"  # OCR / 图片文字识别

DEFAULT_MODEL = "glm-4.7-flash:q4_K_M"   # Planner / Reporter / 表头 LLM 等
DEFAULT_CODER_MODEL = "qwen3-coder:30b"
DEFAULT_ORCHESTRATOR_MODEL = DEFAULT_MODEL   # 无 env 时跟随 DEFAULT_MODEL；部署常用 .env 覆盖为 qwen2.5:7b
DEFAULT_READER_MODEL = "glm-4.7-flash:q4_K_M"         # 默认与 DEFAULT_MODEL 相同
DEFAULT_VISION_MODEL = "deepseek-ocr:latest"
CLINICAL_REPORT_MODEL = "glm-4.7-flash:q4_K_M"        # 默认与 DEFAULT_MODEL 相同

# 主键 OPENAI_API_*；旧名 API_KEY / OPENAI_COMPATIBLE_API_BASE 为兼容别名
OPENAI_API_BASE = "http://192.168.4.110:12716/v1"
OPENAI_API_KEY = ""                 # 本地 Ollama 可用 ollama；空字符串亦可
```

`DEFAULT_ORCHESTRATOR_MODEL` / `DEFAULT_READER_MODEL` / `CLINICAL_REPORT_MODEL` 在代码中默认跟随 `DEFAULT_MODEL`（`os.getenv(..., DEFAULT_MODEL)`）。

### 4.2 `.env` 当前临时配置（文本统一 qwen2.5:7b）

```bash
OPENAI_API_KEY=ollama
OPENAI_API_BASE=http://192.168.4.110:12716/v1
LLM_MODEL=qwen2.5:7b
DEFAULT_MODEL=qwen2.5:7b
DEFAULT_ORCHESTRATOR_MODEL=qwen2.5:7b
DEFAULT_CODER_MODEL=qwen2.5:7b
DEFAULT_READER_MODEL=qwen2.5:7b
DEFAULT_VISION_MODEL=deepseek-ocr:latest
CLINICAL_REPORT_MODEL=qwen2.5:7b
```

兼容别名（可选，一般不必再写）：`API_KEY`、`OPENAI_COMPATIBLE_API_BASE`。业务代码仍可通过 `from configs.config import API_KEY, OPENAI_COMPATIBLE_API_BASE` 使用，二者由上述主键解析。

恢复三模型分工时，将文本相关项改回 `glm-4.7-flash:q4_K_M` / `qwen3-coder:30b`，并保留 `DEFAULT_VISION_MODEL=deepseek-ocr:latest`。

### 4.3 其他与模型相关的环境变量

| 变量 | 默认值 | 含义 |
|---|---|---|
| `READER_ENABLE_LLM_TABLE_HEADER` | `0` | 是否用 LLM 推断 Excel 表头（额外调用） |
| `READER_TABLE_SAMPLE_ROWS` | `5` | 表格样本行数 |
| `READER_TEXT_PREVIEW_CHARS` | `4000` | 文本预览字符上限 |
| `MAX_SUPERVISOR_INVOCATIONS` | `24` | Supervisor 调用上限 |
| `MAX_CODER_CORRECTIONS` | `5` | Coder 修正次数上限 |
| `MAX_PLANNER_RETRIES` | `4` | Planner 重试上限 |

### 4.4 模型名注意事项

- 模型名须与 Ollama **`ollama list` / `/v1/models` 中的 id 完全一致**（含 tag，如 `:30b`、`:q4_K_M`）。
- **不要**在模型名末尾加空格；部分兼容 API 会拒收。
- `SUPPORTED_MODELS` 目前仅作文档性列表，**后端未做强制校验**；以网关实际可用模型为准。

---

## 5. 日志与排障

### 5.1 LLM 调用日志

- 开关：`ENABLE_MODEL_LOG = True`（`config.py`）
- 目录：`{TEMP_FOLDER}/logs/sessions/<session_id>.log`（与 `backend.log` / `frontend.log` 分目录）
- 实现：`src/utils/model_logger.py`（阶段开始/结束、LLM 输入输出等）
- 用途：排障分析管线 / LLM 调用；日常运维可只看 `tmp/logs/backend.log`。不需要时可关 `ENABLE_MODEL_LOG`，旧会话日志可删。

### 5.2 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 流式分析无输出 / 500 | API 地址不可达或模型未 pull | 检查 `.env` 中 `OPENAI_API_BASE`、`curl /v1/models` |
| `model not found` | 名称与 Ollama 不一致或未 pull | 容器内 `ollama list`，对齐 `.env` / 环境变量 |
| Supervisor 决策异常 | 模型过载或 JSON 解析失败 | 查 session 日志；确认 `DEFAULT_ORCHESTRATOR_MODEL`（当前 `qwen2.5:7b`）已 pull 且稳定。本地模型若仍见 `structured_output(json_schema)` / vocabulary 500，确认已用白名单逻辑（§3.2）并重启后端 |
| 图片仅有元数据、无识别文本 | `DEFAULT_VISION_MODEL` 为空或模型不可用 | 确认默认为 `deepseek-ocr:latest` 且 `/v1/models` 可访问 |
| OCR / Vision 调用失败 | 模型不支持 `image_url` 或图片过大 | 确认 `deepseek-ocr:latest` 已部署，或缩小图片后重试 |

---

## 6. 版本记录

| 日期 | 变更 |
|---|---|
| 2026-05-25 | 初版：双模型分工、`medical_ollama` 运维命令、与代码配置映射 |
| 2026-05-25 | `qwen3.6:27b` 上线：通用链路默认切换为 `qwen3.6:27b`，Vision 默认同模型 |
| 2026-07-18 | 通用文本默认改为 `qwen3-coder:30b`；`qwen3.6:27b` 仅用于 Vision；临床链路同步切本地 Ollama |
| 2026-07-18 | Reader Vision 切换为 `deepseek-ocr:latest`：图片 OCR 识别后文本再交通用模型分析 |
| 2026-08-04 | 临时：文本链路（含 Coder/Supervisor/临床）统一为 `qwen2.5:7b`；OCR 仍为 `deepseek-ocr:latest` |
