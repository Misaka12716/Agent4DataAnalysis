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
| `/auth/send-sms-code` | `POST` | 发送短信验证码（登录/注册前置步骤） |
| `/auth/login-with-sms` | `POST` | 短信登录/注册一体：校验后返回user_id |
| `/session/create` | `POST` | 创建会话：生成session_id，关联user_id与工作区 |
| `/session/save-title` | `POST` | 保存会话标题：按 session_id 首次写入标题，已有标题不覆盖 |
| `/session/list` | `GET` | 查询用户会话列表：根据user_id返回 session_id 与标题 |
| `/session/upload-excel` | `POST` | 上传Excel/CSV到会话工作区（会话内数据准备） |
| `/session/workspace-tree` | `GET` | 查询会话工作区目录树：根据session_id返回目录结构与文件归属关系 |
| `/session/snapshot` | `GET` | 获取会话内容快照（查看会话累计内容） |
| `/run-analysis` | `POST` | 发起流式分析任务（基于会话数据的核心业务） |
| `/run-analysis/reconnect` | `POST` | 断线恢复流：先返回锁存快照，再按需续传后续SSE事件 |
| `/health` | `GET` | 健康检查（服务可用性基础校验） |

---

## 3. 详细接口说明
### 3.1 发送短信验证码
- 路径：`POST /auth/send-sms-code`
- 处理函数：`build_send_sms_code_response(phone)`（`src/backend/auth_service.py`）
- Content-Type：`application/json`
- 请求参数（query）：无
- 请求体参数（JSON）：
  - `phone: str`（必填，11 位大陆手机号）
- 请求体示例：
```json
{
  "phone": "18395299120"
}
```
- 成功返回格式：`application/json`
  - `code: int`（`0` 表示成功）
  - `msg: str`
  - `data.phone: str`
  - `data.expires_in: int`（秒）
- 成功返回示例（`200`）：
```json
{
  "code": 0,
  "msg": "SMS code sent successfully",
  "data": {
    "phone": "18395299120",
    "expires_in": 120
  }
}
```
- 常见错误：
  - `400`：手机号为空或格式非法（当前按中国大陆手机号 `^1\\d{10}$` 校验）
  - `502`：短信网关调用失败、网关返回非 JSON、或网关业务返回失败
- 实现逻辑：
  1. 校验手机号格式。
  2. 生成 6 位验证码。
  3. 按短信平台规则生成签名并调用短信网关（`appId + secretKey + timestamp -> md5(sign)`）。
  4. 仅在网关返回成功后，将验证码与过期时间缓存在服务内存中（`120s`）。
  5. 返回 `phone` 与 `expires_in`，不返回验证码明文。

---

### 3.2 短信登录（登录/注册一体）
- 路径：`POST /auth/login-with-sms`
- 处理函数：`build_login_with_sms_response(phone, code)`（`src/backend/auth_service.py`）
- Content-Type：`application/json`
- 请求参数（query）：无
- 请求体参数（JSON）：
  - `phone: str`（必填）
  - `code: str`（必填，短信验证码）
- 请求体示例：
```json
{
  "phone": "18395299120",
  "code": "123456"
}
```
- 成功返回格式：`application/json`
  - `code: int`（`0` 表示成功）
  - `msg: str`
  - `data.user_id: int`
  - `data.username: str`
  - `data.phone: str`
- 成功返回示例（`200`）：
```json
{
  "code": 0,
  "msg": "login success",
  "data": {
    "user_id": 12,
    "username": "user_8000_1713072000",
    "phone": "18395299120"
  }
}
```
- 常见错误：
  - `400`：手机号为空/格式非法，或 `code` 缺失
  - `400`：`code=5`，验证码错误或已过期
  - `403`：用户状态为禁用（兼容 `is_blocked=true` 或 `status in [blocked, disabled, inactive]`）
  - `500`：数据库查询/写入失败
- 实现逻辑（当前版本）：
  1. 校验手机号与验证码参数。
  2. 在服务内存缓存中校验验证码：需存在该手机号验证码记录、未过期（120 秒）、与请求中的 `code` 一致。
  3. 查询 `users` 表：查到则直接登录，查不到则自动创建用户后登录。
  4. 新建用户时自动生成 `username`（`user_<手机号后4位>_<时间戳>`）和占位密码哈希。
  5. 登录成功后消费验证码（从缓存移除），返回用户基础信息。

---

### 3.3 创建会话
- 路径：`POST /session/create`
- 处理函数：`build_create_session_response(user_id: int)`
- Content-Type：`application/json`
- 请求参数（query）：无
- 请求体参数（JSON）：
  - `user_id: int`（必填，正整数）
- 请求体示例：
```json
{
  "user_id": 12
}
```
- 成功返回格式：`application/json`
  - `status: str`
  - `msg: str`
  - `data.session_id: str`
  - `data.user_id: int`
  - `data.workspace_abs_path: str`
- 成功返回示例（`200`）：
```json
{
  "status": "success",
  "msg": "session created",
  "data": {
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "user_id": 12,
    "workspace_abs_path": "/data1/pjw/AgentPlatform/tmp/workspaces/9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
  }
}
```
- 常见错误：
  - `400`：`user_id` 非正整数
  - `404`：`user_id` 在 `users` 表不存在
  - `500`：数据库写入失败
- 实现逻辑：
  1. 校验 `user_id > 0` 且在 `users` 表存在。
  2. 生成 UUID 作为 `session_id`，初始化会话工作区目录。
  3. 写入 `session_user(session_id, user_id, workspace_abs_path)`。
  4. 返回 `session_id`，后续请求只需传该值。

---

### 3.4 查询用户会话列表
- 路径：`GET /session/list`
- 处理函数：`build_user_sessions_response(user_id: int)`
- 请求参数（query）：
  - `user_id: int`（必填，正整数）
- 请求体：无
- 成功返回格式：`application/json`
  - `status: str`
  - `msg: str`
  - `data.user_id: int`
  - `data.sessions: list[object]`
    - `session_id: str`
    - `title: str | null`
- 成功返回示例（`200`）：
```json
{
  "status": "success",
  "msg": "query user sessions success",
  "data": {
    "user_id": 12,
    "sessions": [
      {
        "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
        "title": "Q1 销售分析"
      },
      {
        "session_id": "71e4f870-2d71-4a23-af6d-6cf60c4fe1fd",
        "title": null
      }
    ]
  }
}
```
- 常见错误：
  - `400`：`user_id` 非正整数
  - `404`：`user_id` 在 `users` 表不存在
  - `500`：数据库查询失败
- 实现逻辑：
  1. 校验 `user_id > 0` 且在 `users` 表存在。
  2. 查询 `session_user` 表中该用户对应的全部 `session_id/title`（按创建顺序倒序）。
  3. 返回 `sessions` 数组；无会话时返回空数组。

---

### 3.5 保存会话标题
- 路径：`POST /session/save-title`
- 处理函数：`build_save_session_title_response(session_id: str, title: str)`
- Content-Type：`application/json`
- 请求参数（query）：无
- 请求体参数（JSON）：
  - `session_id: str`（必填）
  - `title: str`（必填，去除首尾空格后不能为空）
- 请求体示例：
```json
{
  "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
  "title": "Q1 销售分析"
}
```
- 成功返回示例（首次写入，`200`）：
```json
{
  "status": "success",
  "msg": "session title saved",
  "data": {
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "title": "Q1 销售分析",
    "saved": true
  }
}
```
- 成功返回示例（已存在标题，不覆盖，`200`）：
```json
{
  "status": "success",
  "msg": "session title already exists",
  "data": {
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "title": "已存在的标题",
    "saved": false
  }
}
```
- 常见错误：
  - `400`：`session_id` 为空或 `title` 为空
  - `404`：`session_id` 不存在
  - `500`：数据库查询/写入失败
- 实现逻辑：
  1. 校验参数。
  2. 校验 `session_id` 存在。
  3. 若 `title` 已有非空值则不覆盖；若为空则执行首次写入。

---

### 3.6 上传会话数据文件
- 路径：`POST /session/upload-excel`
- 处理函数：`handle_session_upload_excel(...)`
- Content-Type：`multipart/form-data`
- 请求参数（query）：无
- 请求体参数（form-data）：
  - `file`：文件（必填，通常为 `xlsx/xls/csv`）
  - `session_id`：字符串（必填）
- 请求体示例（curl）：
```bash
curl -X POST "http://localhost:52716/session/upload-excel" \
  -F "file=@./demo.xlsx" \
  -F "session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
```
- 成功返回格式：`application/json`
  - `status: str`
  - `message: str`
  - `session_id: str`
  - `relative_path: str`
  - `workspace_abs_path: str`
- 成功返回示例（`200`）：
```json
{
  "status": "success",
  "message": "文件已写入会话工作区根目录",
  "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
  "relative_path": "data.xlsx",
  "workspace_abs_path": "/data1/pjw/AgentPlatform/tmp/workspaces/9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
}
```
- 常见错误：
  - `400`：`session_id` 为空
  - `404`：`session_id` 不存在（需先调用 `/session/create`）
  - `413`：文件超过限制（当前 `2048MB`）
  - `500`：写文件失败
- 实现逻辑（关键步骤）：
  1. 校验 `session_id` 非空且已存在。
  2. 校验文件大小不超过 `MAX_FILE_SIZE`。
  3. 按 `data.xxx`/`data_1.xxx` 递增命名避免重名。
  4. 将文件写入会话工作区根目录，更新 `session_user` 表（数据库写入失败不阻断响应）。

---

### 3.7 会话快照查询
- 路径：`GET /session/snapshot`
- 处理函数：`build_session_snapshot_response(session_id: str)`
- 请求参数（query）：
  - `session_id: str`（必填）
- 请求体：无
- 成功返回格式：`application/json`
  - `content: str`，完整累计内容
  - `version: int`，当前最新版本号
- 成功返回示例（`200`）：
```json
{
  "content": "{\"type\":\"orchestrator\",...}\n{\"type\":\"report_chunk\",...}\n",
  "version": 35
}
```
- 常见错误：
  - `404`：`session_id` 不存在（需先调用 `/session/create`）
  - `200` 降级返回：会话存在但内容查询异常时，返回 `content=""`、`version=0`
- 实现逻辑：
  1. 校验 `session_id` 在 `session_user` 中存在。
  2. 调用 `SessionStore.get_latest_content(session_id)`，从 `session_content` 取最大 `version` 对应的完整累计内容。
  3. 查询异常时降级返回空内容和版本 0。

---

### 3.8 会话工作区目录树查询
- 路径：`GET /session/workspace-tree`
- 处理函数：`build_session_workspace_tree_response(session_id: str)`
- 请求参数（query）：
  - `session_id: str`（必填）
- 请求体：无
- 成功返回格式：`application/json`
  - `status: str`
  - `msg: str`
  - `data.session_id: str`
  - `data.workspace_abs_path: str`
  - `data.tree: object`（目录树节点）
    - 目录节点：`name`、`type=directory`、`relative_path`、`children`
    - 文件节点：`name`、`type=file`、`relative_path`、`size`
  - `data.files: list[object]`（实际文件数据）
    - `name: str`
    - `relative_path: str`
    - `size: int`
    - `encoding: "text" | "base64"`
    - `content: str`（`text` 为原文；`base64` 为文件二进制的 Base64 编码）
- 成功返回示例（`200`）：
```json
{
  "status": "success",
  "msg": "query workspace tree success",
  "data": {
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "workspace_abs_path": "/data1/pjw/AgentPlatform/tmp/workspaces/9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "tree": {
      "name": "",
      "type": "directory",
      "relative_path": "",
      "children": [
        {
          "name": "input",
          "type": "directory",
          "relative_path": "input",
          "children": [
            {
              "name": "data.xlsx",
              "type": "file",
              "relative_path": "input/data.xlsx",
              "size": 12045
            }
          ]
        }
      ]
    },
    "files": [
      {
        "name": "data.xlsx",
        "relative_path": "input/data.xlsx",
        "size": 12045,
        "encoding": "base64",
        "content": "UEsDBBQAAAAIAAA..."
      }
    ]
  }
}
```
- 常见错误：
  - `400`：`session_id` 为空
  - `404`：`session_id` 不存在
  - `500`：会话查询失败、工作区路径缺失、或目录树构建失败
- 实现逻辑：
  1. 校验 `session_id` 非空并存在于 `session_user`。
  2. 读取该会话 `workspace_abs_path`。
  3. 递归扫描目录，返回目录与文件的层级关系（`tree`）。
  4. 同时读取所有实际文件并返回内容（`files`；文本为 UTF-8 原文，二进制为 Base64）。
  5. 若工作区目录不存在，返回空树与空文件列表。

---

### 3.9 流式分析任务（SSE）
- 路径：`POST /run-analysis`
- 处理函数：`build_run_analysis_response(body: StreamingTaskRequest)`
- Content-Type：`application/json`
- 响应类型：`text/event-stream`
- 请求参数（query）：无
- 请求体参数（JSON）：
  - `session_id: str`（必填）
  - `input_data: str`（必填）
- 请求体示例：
```json
{
  "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
  "input_data": "请对工作区目录下的数据做统计分析并给出结论。"
}
```
- 成功返回格式：`text/event-stream`（SSE）
  - 每条事件：`data: <json>\n\n`
  - `<json>` 常见字段：`type`、`content`、`error` 等
- 成功返回示例（片段）：
```text
data: {"type":"report_chunk","content":"第一部分结论..."}
```
- 常见错误：
  - `404`：`session_id` 不存在（需先调用 `/session/create`）
  - 流中事件 `type=streaming_error` / `type=error`：任务执行异常
- 实现逻辑（端到端）：
  1. 校验 `session_id` 存在，返回带 SSE 头的 `StreamingResponse`。
  2. 内部调用 `run_orchestrated_analysis_stream(...)` 生成流式事件。
  3. 每条事件先写入 `session_content`（版本递增），再推送给前端。
  4. 正常结束补发 `streaming_ended`，异常时发送 `streaming_error`。

---

### 3.10 断线恢复流（SSE）
- 路径：`POST /run-analysis/reconnect`
- 处理函数：`build_reconnect_analysis_response(session_id: str)`
- Content-Type：`application/json`
- 响应类型：`text/event-stream`
- 请求体参数（JSON）：
  - `session_id: str`（必填）
- 流式返回语义：
  1. 第一条固定返回 `type=snapshot`，包含 `content`（当前完整锁存内容）和 `version`（当前版本号）。
  2. 若快照末尾已是终态事件（`streaming_ended/error/streaming_error`），连接立即结束。
  3. 若分析尚未结束，后端继续轮询数据库并推送新增事件，直到终态事件再结束。
- 成功返回示例（首条）：
```text
data: {"type":"snapshot","session_id":"...","content":"...","version":35,"timestamp":"2026-04-21 12:00:00"}
```
- 常见错误：
  - `400`：`session_id` 为空
  - `404`：`session_id` 不存在
  - `500`：启动重连流失败

---

### 3.11 健康检查
- 路径：`GET /health`
- 处理函数：`build_health_response()`（`src/backend/route_services.py`）
- 请求参数：无
- 请求体：无
- 成功返回格式：`application/json`
  - `status: str`，服务健康状态
  - `service: str`，服务标识
  - `version: str`，服务版本
- 成功返回示例（`200`）：
```json
{
  "status": "healthy",
  "service": "agent-workflow-server",
  "version": "1.1"
}
```
- 常见错误：通常无业务错误；仅在进程不可达时由网关/客户端侧报连接失败
- 实现逻辑：
  1. 进入 `health_check` 路由。
  2. 调用 `build_health_response()` 返回固定健康信息。
  3. 不依赖数据库或模型服务可用性。

---

## 4. 数据存储与接口关系

- `session_user`：
  - 由 `/session/create` 创建并维护
  - 记录 `session_id -> user_id + title + workspace_abs_path`
  - `/session/save-title` 按 `session_id` 首次写入 `title`
  - `/session/list` 按 `user_id` 读取其全部 `session_id/title`
- `session_content`：
  - 由 `/run-analysis` 的流式过程持续写入
  - 每条事件对应一个新版本（`version` 递增）
  - `content` 存放“完整累计内容”文本
- `/session/snapshot`：
  - 读取 `session_content` 最新版本并返回
- `users`：
  - 由 `/auth/login-with-sms` 在首次手机号登录时自动写入
  - 已存在手机号走直接登录，不重复创建

---

## 5. 调用示例
### 5.1 发送短信验证码
```bash
curl -X POST "http://localhost:52716/auth/send-sms-code" \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "18395299120"
  }'
```

### 5.2 短信登录（自动注册）
```bash
curl -X POST "http://localhost:52716/auth/login-with-sms" \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "18395299120",
    "code": "123456"
  }'
```

### 5.3 创建会话
```bash
curl -X POST "http://localhost:52716/session/create" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 12
  }'
```

### 5.4 查询用户会话列表
```bash
curl "http://localhost:52716/session/list?user_id=12"
```

### 5.5 保存会话标题
```bash
curl -X POST "http://localhost:52716/session/save-title" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "title": "Q1 销售分析"
  }'
```

### 5.6 上传数据文件
```bash
curl -X POST "http://localhost:52716/session/upload-excel" \
  -F "file=@./demo.xlsx" \
  -F "session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
```

### 5.7 查询快照
```bash
curl "http://localhost:52716/session/snapshot?session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
```

### 5.8 查询会话工作区目录树
```bash
curl "http://localhost:52716/session/workspace-tree?session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
```

### 5.9 发起流式分析
```bash
curl -N -X POST "http://localhost:52716/run-analysis" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "input_data": "请对工作区目录下的数据做统计分析并给出结论。"
  }'
```

### 5.10 健康检查
```bash
curl http://localhost:52716/health
```

---

## 6. 说明与注意事项

- 目前 `upload-excel` 的后缀类型未在接口层强限制，实际由上游调用约定为 Excel/CSV。
- 上传接口大小限制为 `2048MB`，请同时确认反向代理（如 Nginx）配置一致。
- SSE 是持续连接，前端需按流式协议处理 `data:` 行。
- 会话内容按“完整累计文本”落库，体量较大时可考虑后续改为增量片段存储策略。
- 短信验证码存储在服务内存中，服务重启后验证码会丢失；如需多实例部署，建议迁移到 Redis 等集中缓存。
