# 大模型部署与配置说明

本文档汇总 AgentPlatform 所用大模型的**部署方式**、**模型清单**、**分工策略**与**平台内配置映射**，便于运维与开发统一查阅。

---

## 1. 架构概览

平台通过 **OpenAI 兼容 HTTP API** 调用本地模型，不直接在应用进程内加载权重。调用链如下：

```text
AgentPlatform (LangChain ChatOpenAI)
    → OPENAI_COMPATIBLE_API_BASE (/v1)
    → Docker 内 Ollama（容器 medical_ollama）
    → 各模型权重（pull 到容器内）
```

- **网关地址**（当前开发配置）：`http://192.168.4.110:12716/v1`
- **协议**：与 OpenAI Chat Completions 兼容（`POST /v1/chat/completions`、可选 `GET /v1/models`）
- **指纹**：响应中可见 `system_fingerprint: fp_ollama`，表明后端为 Ollama 兼容服务
- **应用侧配置入口**：[`src/configs/config.py`](../src/configs/config.py)

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
# 代码专用
ollama pull qwen3-coder:30b

# 通用 + 多模态
ollama pull qwen3.6:27b
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
    "model": "qwen3-coder:30b",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 16
  }'
```

**当前 API 已注册模型**（`GET /v1/models`，以线上为准）：

| 模型 ID | 状态 | 用途 |
|---|---|---|
| `qwen3-coder:30b` | 已部署 | **代码生成 / 修正**（Coder 专用） |
| `qwen3.6:27b` | 已部署 | **通用推理 + 多模态**（Planner / Orchestrator / Reader / Reporter / Vision） |
| `qwen2.5:14b` | 已部署 | 轻量备用 |
| `qwen2.5:7b` | 已部署 | 历史备用 |

---

## 3. 模型分工策略

采用**双模型**策略：Coder 与通用链路分离，避免用大代码模型做摘要/路由，也避免用通用模型写长代码。

| 角色 | 推荐模型 | 说明 |
|---|---|---|
| **Coder**（代码生成、失败修正） | `qwen3-coder:30b` | 固定使用 `DEFAULT_CODER_MODEL` |
| **Planner**（需求解析、步骤分解） | `qwen3.6:27b` | 流式 LLM，使用 `DEFAULT_MODEL` |
| **Orchestrator / Supervisor**（路由决策） | `qwen3.6:27b` | 可用 `DEFAULT_ORCHESTRATOR_MODEL` 覆盖 |
| **Reader**（工作区摘要压缩） | `qwen3.6:27b` | `DEFAULT_READER_MODEL` |
| **Reader Vision**（图片理解） | `qwen3.6:27b` | `DEFAULT_VISION_MODEL`，需支持 `image_url` 多模态 |
| **Reporter**（最终报告） | `qwen3.6:27b` | 使用 `DEFAULT_MODEL` |
| **Reader 表头 LLM**（可选） | `qwen3.6:27b` | 仅当 `READER_ENABLE_LLM_TABLE_HEADER=1` |
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

### 3.2 Supervisor 与 Qwen 系列的兼容性

`qwen` 系列在部分 OpenAI 兼容网关上**不支持** `response_format=json_schema`。编排层已对模型名包含 `qwen` 等情况跳过 json_schema，优先 `function_calling` 与原始 JSON 解析回退（见 `analysis_pipeline_graph.py` 中 `_should_try_json_schema_method`）。部署新模型后一般无需改此逻辑。

---

## 4. 平台配置（`config.py` 与环境变量）

### 4.1 当前默认配置（`src/configs/config.py`）

```python
GENERAL_MODEL = "qwen3.6:27b"       # 通用 / 多模态
CODER_MODEL = "qwen3-coder:30b"     # Coder 专用

DEFAULT_MODEL = "qwen3.6:27b"       # Planner / Reporter / 表头 LLM 等
DEFAULT_CODER_MODEL = "qwen3-coder:30b"
DEFAULT_ORCHESTRATOR_MODEL = "qwen3.6:27b"   # 默认与 DEFAULT_MODEL 相同
DEFAULT_READER_MODEL = "qwen3.6:27b"
DEFAULT_VISION_MODEL = "qwen3.6:27b"

OPENAI_COMPATIBLE_API_BASE = "http://192.168.4.110:12716/v1"
API_KEY = ""                        # 本地 Ollama 通常为空
```

以上默认值均可通过环境变量覆盖（见 §4.2）。

### 4.2 环境变量覆盖（可选）

如需临时切换模型，可在启动后端前导出，例如回退到代码模型做全链路调试：

```bash
export DEFAULT_MODEL=qwen3-coder:30b
export DEFAULT_ORCHESTRATOR_MODEL=qwen3-coder:30b
export DEFAULT_READER_MODEL=qwen3-coder:30b
export DEFAULT_VISION_MODEL=qwen3.6:27b
export DEFAULT_CODER_MODEL=qwen3-coder:30b
```

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

- 模型名须与 Ollama **`ollama list` / `/v1/models` 中的 id 完全一致**（含 tag，如 `:30b`、`:27b`）。
- **不要**在模型名末尾加空格；部分兼容 API 会拒收。
- `SUPPORTED_MODELS` 目前仅作文档性列表，**后端未做强制校验**；以网关实际可用模型为准。

---

## 5. 日志与排障

### 5.1 LLM 调用日志

- 开关：`ENABLE_MODEL_LOG = True`（`config.py`）
- 目录：`{TEMP_FOLDER}/logs/<session_id>.log`
- 实现：`src/utils/model_logger.py`（阶段开始/结束、LLM 输入输出等）

### 5.2 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 流式分析无输出 / 500 | API 地址不可达或模型未 pull | 检查 `OPENAI_COMPATIBLE_API_BASE`、`curl /v1/models` |
| `model not found` | 名称与 Ollama 不一致或未 pull | 容器内 `ollama list`，对齐 config / 环境变量 |
| Supervisor 决策异常 | 模型过载或 JSON 解析失败 | 查 session 日志；确认 `qwen3.6` 已部署且稳定 |
| 图片仅有元数据、无描述 | `DEFAULT_VISION_MODEL` 为空或模型不可用 | 确认默认为 `qwen3.6:27b` 且 `/v1/models` 可访问 |
| Vision 调用失败 | 模型不支持多模态或图片过大 | 换视觉模型或缩小图片 |

---

## 6. 版本记录

| 日期 | 变更 |
|---|---|
| 2026-05-25 | 初版：双模型分工、`medical_ollama` 运维命令、与代码配置映射 |
| 2026-05-25 | `qwen3.6:27b` 上线：通用链路默认切换为 `qwen3.6:27b`，Vision 默认同模型 |
