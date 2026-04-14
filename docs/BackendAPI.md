# 后端接口说明

本文档基于当前 `src/backend` 与相关存储实现整理，目标是说明：

- 后端提供了哪些接口
- 每个接口的请求/返回格式
- 关键实现逻辑（包含会话持久化与流式输出）

---

## 1. 服务基础信息

- 服务框架：FastAPI
- 入口文件：`src/backend/server.py`
- 默认端口：`52716`
- 服务版本：`1.1`
- CORS：`allow_origins=["*"]`，`allow_methods=["*"]`，`allow_headers=["*"]`

---

## 2. 接口总览

| 接口 | 方法 | 说明 |
|---|---|---|
| `/health` | `GET` | 健康检查 |
| `/session/upload-excel` | `POST` | 上传 Excel/CSV 到会话工作区 |
| `/session/snapshot` | `GET` | 获取会话内容快照（完整累计内容 + 当前版本） |
| `/run-analysis` | `POST` | 发起流式分析任务（SSE） |

---

## 3. 详细接口说明

## 3.1 健康检查

- 路径：`GET /health`
- 处理函数：`build_health_response()`（`src/backend/route_services.py`）

返回示例（`200`）：

```json
{
  "status": "healthy",
  "service": "agent-workflow-server",
  "version": "1.1"
}
```

实现逻辑：

- 固定返回服务状态，不依赖数据库或模型服务可用性。

---

## 3.2 上传会话数据文件

- 路径：`POST /session/upload-excel`
- 处理函数：`handle_session_upload_excel(...)`
- Content-Type：`multipart/form-data`

请求参数（form-data）：

- `file`：文件（必填，通常为 `xlsx/xls/csv`）
- `session_id`：字符串（必填）
- `user_id`：整数（可选，默认 `0`）

成功返回（`200`）示例：

```json
{
  "status": "success",
  "message": "文件已写入会话工作区根目录",
  "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
  "relative_path": "data.xlsx",
  "workspace_abs_path": "/data/agent_platform/tmp/workspaces/9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
}
```

常见错误：

- `400`：`session_id` 为空
- `413`：文件超过限制（当前 `2048MB`）
- `500`：写文件失败

实现逻辑（关键步骤）：

1. 校验 `session_id` 非空。
2. 先遍历文件流计算大小，超过 `MAX_FILE_SIZE` 直接报错。
3. 调用 `init_workspace(session_id)` 初始化/获取工作区目录。
4. 调用 `generate_data_filename(...)` 统一命名，按 `data.xxx`、`data_1.xxx` 递增避免重名。
5. 将文件写入会话工作区根目录。
6. 调用 `SessionStore.set_workspace_path(session_id, user_id, workspace_abs_path)` 写入/更新 `session_user` 表。
7. 返回保存结果。

备注：

- 第 6 步如果数据库写入失败，当前实现不会阻断上传成功响应（接口仍返回 200）。

---

## 3.3 会话快照查询

- 路径：`GET /session/snapshot`
- 处理函数：`build_session_snapshot_response(session_id: str)`

请求参数（query）：

- `session_id`：字符串（必填）

成功返回（`200`）示例：

```json
{
  "content": "{\"type\":\"orchestrator\",...}\n{\"type\":\"report_chunk\",...}\n",
  "version": 35
}
```

实现逻辑：

1. 调用 `SessionStore.get_latest_content(session_id)`。
2. 从 `session_content` 取该会话最大 `version` 对应的 `content`（即完整累计内容）。
3. 查询异常时降级返回：
   - `content=""`
   - `version=0`

---

## 3.4 流式分析任务（SSE）

- 路径：`POST /run-analysis`
- 处理函数：`build_run_analysis_response(body: StreamingTaskRequest)`
- Content-Type：`application/json`
- 响应类型：`text/event-stream`

请求体（JSON）：

```json
{
  "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
  "input_data": "请对工作区目录下的数据做统计分析并给出结论。"
}
```

请求模型：

- `StreamingTaskRequest`
  - `session_id: str`
  - `input_data: str`

SSE 返回格式：

- 每条事件为一行：
  - `data: <json>\n\n`
- `<json>` 为业务 payload，常见 `type`：
  - `orchestrator`
  - `planner`
  - `coder`
  - `worker`
  - `report_chunk`
  - `streaming_ended`
  - `streaming_error`
  - `error`

`report_chunk` 示例：

```text
data: {"type":"report_chunk","content":"第一部分结论..."}

```

实现逻辑（端到端）：

1. 接口返回 `StreamingResponse(streaming_task_generator(...))`，并设置 SSE 头：
   - `Cache-Control: no-cache`
   - `X-Accel-Buffering: no`
2. `streaming_task_generator` 内部调用编排入口 `run_orchestrated_analysis_stream(...)`。
3. 每收到一个阶段事件 payload：
   - 先 `_push_to_session`：
     - `json.dumps(payload)` 后追加换行
     - 调用 `SessionStore.append_content(session_id, fragment + "\n")`
     - 写入 `session_content` 新版本（完整累计内容）
   - 再 `yield` 给前端（SSE `data:` 行）
4. 若流式过程未出现 `streaming_error` / `error`，结尾补发：
   - `{"type":"streaming_ended", ...}`
5. 若生成过程抛异常，发送：
   - `{"type":"streaming_error","error":"..."}`。

---

## 4. 数据存储与接口关系

- `session_user`：
  - 由 `/session/upload-excel` 维护
  - 记录 `session_id -> user_id + workspace_abs_path`
- `session_content`：
  - 由 `/run-analysis` 的流式过程持续写入
  - 每条事件对应一个新版本（`version` 递增）
  - `content` 存放“完整累计内容”文本
- `/session/snapshot`：
  - 读取 `session_content` 最新版本并返回

---

## 5. 调用示例

## 5.1 健康检查

```bash
curl http://localhost:52716/health
```

## 5.2 上传数据文件

```bash
curl -X POST "http://localhost:52716/session/upload-excel" \
  -F "file=@./demo.xlsx" \
  -F "session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73" \
  -F "user_id=0"
```

## 5.3 查询快照

```bash
curl "http://localhost:52716/session/snapshot?session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
```

## 5.4 发起流式分析

```bash
curl -N -X POST "http://localhost:52716/run-analysis" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "input_data": "请对工作区目录下的数据做统计分析并给出结论。"
  }'
```

---

## 6. 说明与注意事项

- 目前 `upload-excel` 的后缀类型未在接口层强限制，实际由上游调用约定为 Excel/CSV。
- 上传接口大小限制为 `2048MB`，请同时确认反向代理（如 Nginx）配置一致。
- SSE 是持续连接，前端需按流式协议处理 `data:` 行。
- 会话内容按“完整累计文本”落库，体量较大时可考虑后续改为增量片段存储策略。
