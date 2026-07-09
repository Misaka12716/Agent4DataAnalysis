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
- 鉴权：JWT Bearer Token（详见 [AUTH.md](AUTH.md) 与下文 §1.1）

### 1.1 鉴权说明

除 `/health`、`/auth/send-sms-code`、`/auth/login-with-sms` 外，**所有接口均需登录**。

- 登录成功后响应 `data.access_token`
- 后续请求携带请求头：`Authorization: Bearer <access_token>`
- 服务端从 token 解析当前用户，**不再接受**客户端传入的 `user_id`
- 会话与项目采用 RBAC：所有者、项目成员或平台 admin 可访问（详见 [RBAC.md](RBAC.md)）
- 已归档项目（`projects.status=archived`）禁止：新建会话、上传、发起分析

| HTTP 状态 | code | 含义 |
|---|---|---|
| 401 | 6 | 未携带 token、token 无效或已过期 |
| 403 | 3 | 用户已被封禁 |
| 403 | 7 | token 有效，但 session / project 不属于当前用户且非项目成员 |
| 403 | 8 | 项目已归档，禁止写操作 |
| 403 | 9 | 权限不足（缺少对应操作权限码） |

---

## 2. 接口总览

| 接口 | 方法 | 说明 |
|---|---|---|
| `/auth/send-sms-code` | `POST` | 发送短信验证码（登录/注册前置步骤，公开） |
| `/auth/login-with-sms` | `POST` | 短信登录/注册一体：校验后返回 JWT 与用户信息（公开） |
| `/auth/me` | `GET` | 获取当前登录用户信息（需 Bearer Token） |
| `/auth/update-username` | `POST` | 修改当前用户姓名/昵称（需 Bearer Token） |
| `/project/create` | `POST` | 创建项目（需 Bearer Token） |
| `/project/list` | `GET` | 查询当前用户项目列表（需 Bearer Token） |
| `/project/{project_id}` | `GET` | 查询项目详情（需 Bearer Token + 项目归属） |
| `/project/{project_id}` | `PUT` | 重命名项目（需 Bearer Token + 项目归属；个人默认不可改名） |
| `/project/{project_id}/tree` | `GET` | 查询项目 raw/outputs/archive 目录树 |
| `/project/{project_id}/archive` | `POST` | 归档项目（需 Bearer Token + 项目归属） |
| `/project/{project_id}/restore` | `POST` | 恢复项目（需 Bearer Token + 项目归属） |
| `/project/{project_id}/upload` | `POST` | 上传文件到项目 raw/ 目录（需 Bearer Token + 项目归属） |
| `/project/{project_id}/assets` | `GET` | 查询项目资产列表（需 Bearer Token + 项目归属） |
| `/project/{project_id}/sessions` | `GET` | 查询项目下会话列表（需 Bearer Token + 项目归属） |
| `/session/create` | `POST` | 创建会话：在指定项目下生成 session_id 与工作区（需 Bearer Token + project_id） |
| `/session/save-title` | `POST` | 保存会话标题：按 session_id 首次写入标题（需 Bearer Token + 会话归属） |
| `/session/list` | `GET` | 查询可访问会话列表；可选 `?project_id=` 过滤（需 Bearer Token） |
| `/session/meta` | `GET` | 查询会话元数据（project_id、工作区路径等） |
| `/session/copy-from-project-raw` | `POST` | 将项目 raw/ 文件复制到会话工作区（分析入口） |
| `/session/upload-excel` | `POST` | 上传文件到会话工作区（需 Bearer Token + 会话归属） |
| `/session/workspace-tree` | `GET` | 查询会话工作区目录树（需 Bearer Token + 会话归属） |
| `/session/snapshot` | `GET` | 获取会话内容快照（需 Bearer Token + 会话归属） |
| `/run-analysis` | `POST` | 发起流式分析任务（需 Bearer Token + 会话归属） |
| `/run-analysis/reconnect` | `POST` | 断线恢复流（需 Bearer Token + 会话归属） |
| `/health` | `GET` | 健康检查（公开） |

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
  - `data.access_token: str`（JWT，后续请求放入 `Authorization: Bearer`）
  - `data.token_type: str`（固定为 `bearer`）
  - `data.expires_in: int`（秒，默认 7 天）
  - `data.user_id: int`
  - `data.username: str`
  - `data.phone: str`
- 成功返回示例（`200`）：
```json
{
  "code": 0,
  "msg": "login success",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "expires_in": 604800,
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
  5. 登录成功后消费验证码（从缓存移除），签发 JWT 并返回用户基础信息。

---

### 3.3 获取当前用户信息
- 路径：`GET /auth/me`
- 处理函数：`build_me_response(...)`（`src/backend/auth_service.py`）
- 鉴权：`Authorization: Bearer <access_token>`（必填）
- 成功返回示例（`200`）：
```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "user_id": 12,
    "username": "张三",
    "phone": "18395299120"
  }
}
```
- 常见错误：
  - `401`：`code=6`，未登录或 token 无效/过期

---

### 3.4 修改用户姓名/昵称
- 路径：`POST /auth/update-username`
- 处理函数：`build_update_username_response(user_id, username)`（`src/backend/auth_service.py`）
- 鉴权：`Authorization: Bearer <access_token>`（必填）
- Content-Type：`application/json`
- 请求体参数（JSON）：
  - `username: str`（必填，用户姓名/昵称，对应 `users.username`，最长 128 字符）
- 请求体示例：
```json
{
  "username": "张三"
}
```
- 成功返回格式：`application/json`
  - `code: int`（`0` 表示成功）
  - `msg: str`
  - `data.user_id: int`
  - `data.username: str`
- 成功返回示例（`200`）：
```json
{
  "code": 0,
  "msg": "username updated",
  "data": {
    "user_id": 12,
    "username": "张三"
  }
}
```
- 常见错误：
  - `401`：`code=6`，未登录或 token 无效/过期
  - `400`：`username` 为空/超长
  - `403`：用户状态为禁用
  - `409`：`username` 已被其他用户占用
  - `500`：数据库查询/更新失败
- 实现逻辑：
  1. 从 Bearer Token 解析当前 `user_id`。
  2. 校验 `username` 去空格后非空、长度不超过 128。
  3. 校验用户未被禁用。
  4. 若新名称与当前相同，直接返回成功（幂等）。
  5. 查重并执行 `UPDATE users SET username = ? WHERE id = ?`。

---

### 3.5 创建会话
- 路径：`POST /session/create`
- 处理函数：`build_create_session_response(user_id, project_id)`
- 鉴权：`Authorization: Bearer <access_token>`（必填）
- Content-Type：`application/json`
- 请求体参数（JSON）：
  - `project_id: int`（**可选**；省略时自动归入该用户的「个人默认」项目 `__personal_default__`）
- 请求体示例：
```json
{
  "project_id": 1
}
```
- 省略 `project_id` 时也可传空对象 `{}`。
- 成功返回格式：`application/json`
  - `status: str`
  - `msg: str`
  - `data.session_id: str`
  - `data.user_id: int`
  - `data.project_id: int`
  - `data.workspace_abs_path: str`（会话工作区绝对路径，形如 `.../workspaces/<user_id>/<project_id>/sessions/<session_id>`）
- 成功返回示例（`200`）：
```json
{
  "status": "success",
  "msg": "session created",
  "data": {
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "user_id": 12,
    "project_id": 1,
    "workspace_abs_path": "/data1/pjw/AgentPlatform/tmp/workspaces/12/1/sessions/9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
  }
}
```
- 常见错误：
  - `401`：`code=6`，未登录或 token 无效/过期
  - `403`：`code=8`，项目已归档
  - `500`：数据库写入失败
- 实现逻辑：
  1. 从 Bearer Token 解析当前 `user_id`。
  2. 若未传 `project_id`，自动解析/创建「个人默认」项目。
  3. 校验项目归属且未归档。
  4. 所有新项目（含「个人默认」）统一使用 `.../<project_id>/sessions/<session_id>/` 布局；历史 legacy 路径由 `resolve_workspace_root` 只读兼容，可通过 `scripts/migrate-legacy-sessions.sh` 迁移。

---

### 3.5.1 查询会话元数据
- 路径：`GET /session/meta`
- 鉴权：`Authorization: Bearer <access_token>`（必填）
- 请求参数（query）：
  - `session_id: str`（必填）
- 成功返回字段（`data`）：
  - `session_id`, `user_id`, `project_id`, `title`, `workspace_abs_path`
- 用途：前端切换会话时同步项目上下文与权限。

---

### 3.5.2 从项目 raw/ 复制到会话工作区
- 路径：`POST /session/copy-from-project-raw`
- 鉴权：Bearer + 会话 `数据上传` 权限
- 请求体（JSON）：
  - `session_id: str`（必填）
  - `relative_paths: list[str]`（可选；默认复制 `raw/` 下全部文件）
- 说明：分析链路只读会话工作区；项目 `raw/` 预置文件需经此接口或会话上传进入分析。

---

### 3.6 查询用户会话列表
- 路径：`GET /session/list`
- 处理函数：`build_user_sessions_response(user_id, project_id?)`
- 鉴权：`Authorization: Bearer <access_token>`（必填）
- 请求参数（query）：
  - `project_id: int`（**可选**；传入时仅返回归属该项目的可访问会话）
- 请求体：无
- 成功返回格式：`application/json`
  - `status: str`
  - `msg: str`
  - `data.user_id: int`
  - `data.sessions: list[object]`
    - `session_id: str`
    - `title: str | null`
    - `project_id: int | null`
    - `access: str`（`owner` | `shared`）
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
        "title": "Q1 销售分析",
        "project_id": 1,
        "access": "owner"
      },
      {
        "session_id": "71e4f870-2d71-4a23-af6d-6cf60c4fe1fd",
        "title": null,
        "project_id": 2,
        "access": "shared"
      }
    ]
  }
}
```
- 常见错误：
  - `401`：`code=6`，未登录或 token 无效/过期
  - `500`：数据库查询失败
- 实现逻辑：
  1. 从 Bearer Token 解析当前 `user_id`。
  2. 合并「自己创建的」与「可访问项目内的共享」会话；可选按 `project_id` 过滤。
  3. `GET /project/{id}/sessions` 仍可用，语义等价于 `GET /session/list?project_id={id}`（需项目读权限）。

---

### 3.7 保存会话标题
- 路径：`POST /session/save-title`
- 处理函数：`build_save_session_title_response(session_id: str, title: str, current_user_id: int)`
- 鉴权：`Authorization: Bearer <access_token>`（必填）；且 session 须属于当前用户
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
  - `401`：`code=6`，未登录或 token 无效/过期
  - `403`：`code=7`，session 不属于当前用户
  - `400`：`session_id` 为空或 `title` 为空
  - `404`：`session_id` 不存在
  - `500`：数据库查询/写入失败
- 实现逻辑：
  1. 校验参数与 Bearer Token。
  2. 校验 `session_id` 存在且 `session_user.user_id` 等于当前用户。
  3. 若 `title` 已有非空值则不覆盖；若为空则执行首次写入。

---

### 3.8 上传会话文件（多模态）

- 路径：`POST /session/upload-excel`（历史路径名保留；实际支持表格 / 图片 / 文本，与 Reader 分类一致）
- 处理函数：`handle_session_upload_excel(...)`
- 鉴权：`Authorization: Bearer <access_token>`（必填）；且 session 须属于当前用户
- Content-Type：`multipart/form-data`
- 请求参数（query）：无
- 请求体参数（form-data）：
  - `file`：文件（必填）
  - `session_id`：字符串（必填）
- **允许扩展名（白名单）**：
  - **table**：`.xlsx`、`.xls`、`.csv`、`.tsv`
  - **image**：`.png`、`.jpg`、`.jpeg`、`.gif`、`.webp`、`.bmp`（Reader 可走 Vision 多模态，需配置 `DEFAULT_VISION_MODEL`）
  - **text**：`.txt`、`.md`、`.json`、`.yaml`、`.yml`、`.log`、`.xml`、`.html`、`.htm`
  - 其余类型（如 `.pdf`）返回 `415`
- 请求体示例（curl）：
```bash
curl -X POST "http://localhost:52716/session/upload-excel" \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@./demo.xlsx" \
  -F "session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
```
- 成功返回格式：`application/json`
  - `status: str`
  - `message: str`
  - `session_id: str`
  - `relative_path: str`
  - `original_filename: str`
  - `file_category: str`（`table` / `image` / `text`）
  - `workspace_abs_path: str`（会话工作区绝对路径）
- 成功返回示例（`200`）：
```json
{
  "status": "success",
  "message": "文件已写入会话工作区根目录",
  "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
  "relative_path": "data.xlsx",
  "original_filename": "demo.xlsx",
  "file_category": "table",
  "workspace_abs_path": "/data1/pjw/AgentPlatform/tmp/workspaces/12/9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
}
```
- 常见错误：
  - `401`：`code=6`，未登录或 token 无效/过期
  - `403`：`code=7`，session 不属于当前用户
  - `400`：`session_id` 为空
  - `404`：`session_id` 不存在（需先调用 `/session/create`）
  - `415`：扩展名不在白名单
  - `413`：文件超过限制（当前 `2048MB`）
  - `500`：写文件失败
- 实现逻辑（关键步骤）：
  1. 校验 `session_id` 非空、归属当前用户且已存在。
  2. 校验扩展名在 Reader 支持的 table/image/text 白名单内。
  3. 校验文件大小不超过 `MAX_FILE_SIZE`。
  4. 按 `data.xxx`/`data_1.xxx` 递增命名避免重名。
  5. 经 `ensure_runtime(session_id).files.write` 写入工作区，并刷新 `SESSION_MEMORY.md` 快照。
  6. 响应中的 `workspace_abs_path` 为 DB 中记录的工作区路径。

---

### 3.9 会话快照查询
- 路径：`GET /session/snapshot`
- 处理函数：`build_session_snapshot_response(session_id: str, current_user_id: int)`
- 鉴权：`Authorization: Bearer <access_token>`（必填）；且 session 须属于当前用户
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
  - `401`：`code=6`，未登录或 token 无效/过期
  - `403`：`code=7`，session 不属于当前用户
  - `404`：`session_id` 不存在（需先调用 `/session/create`）
  - `200` 降级返回：会话存在但内容查询异常时，返回 `content=""`、`version=0`
- 实现逻辑：
  1. 校验 Bearer Token 与 session 归属。
  2. 调用 `SessionStore.get_latest_content(session_id)`，从 `session_content` 取最大 `version` 对应的完整累计内容。
  3. 查询异常时降级返回空内容和版本 0。

---

### 3.10 会话工作区目录树查询
- 路径：`GET /session/workspace-tree`
- 处理函数：`build_session_workspace_tree_response(session_id: str, current_user_id: int)`
- 鉴权：`Authorization: Bearer <access_token>`（必填）；且 session 须属于当前用户
- 请求参数（query）：
  - `session_id: str`（必填）
- 请求体：无
- 成功返回格式：`application/json`
  - `status: str`
  - `msg: str`
  - `data.session_id: str`
  - `data.workspace_abs_path: str`（会话工作区绝对路径）
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
    "workspace_abs_path": "/data1/pjw/AgentPlatform/tmp/workspaces/12/9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
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
  - `401`：`code=6`，未登录或 token 无效/过期
  - `403`：`code=7`，session 不属于当前用户
  - `400`：`session_id` 为空
  - `404`：`session_id` 不存在
  - `500`：会话查询失败、工作区路径缺失、或目录树构建失败
- 实现逻辑：
  1. 校验 Bearer Token 与 session 归属。
  2. 读取该会话 `workspace_abs_path`（`workspaces/<user_id>/<session_id>/`）。
  3. 递归扫描工作区目录，返回目录与文件的层级关系（`tree`）。
  4. 同时读取所有实际文件并返回内容（`files`；文本为 UTF-8 原文，二进制为 Base64）。
  5. 若工作区目录不存在，返回空树与空文件列表。

> 说明：默认本地 Runtime 下，工作区目录即为真实存储；启用 Cube Sandbox 时，沙箱适配器写文件后会 sync 到同一镜像路径供 Reader / workspace-tree 读取。

---

### 3.11 流式分析任务（SSE）
- 路径：`POST /run-analysis`
- 处理函数：`build_run_analysis_response(body: StreamingTaskRequest, current_user_id: int)`
- 鉴权：`Authorization: Bearer <access_token>`（必填）；且 session 须属于当前用户
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
  - `401`：`code=6`，未登录或 token 无效/过期
  - `403`：`code=7`，session 不属于当前用户
  - `404`：`session_id` 不存在（需先调用 `/session/create`）
  - 流中事件 `type=streaming_error` / `type=error`：任务执行异常
- 实现逻辑（端到端）：
  1. 校验 Bearer Token 与 session 归属，返回带 SSE 头的 `StreamingResponse`。
  2. 内部调用 `run_orchestrated_analysis_stream(...)` 生成流式事件。
  3. 每条事件先写入 `session_content`（版本递增），再推送给前端。
  4. 正常结束补发 `streaming_ended`，异常时发送 `streaming_error`。

---

### 3.12 断线恢复流（SSE）
- 路径：`POST /run-analysis/reconnect`
- 处理函数：`build_reconnect_analysis_response(session_id: str, current_user_id: int)`
- 鉴权：`Authorization: Bearer <access_token>`（必填）；且 session 须属于当前用户
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
  - `401`：`code=6`，未登录或 token 无效/过期
  - `403`：`code=7`，session 不属于当前用户
  - `400`：`session_id` 为空
  - `404`：`session_id` 不存在
  - `500`：启动重连流失败

---

### 3.13 健康检查
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

### 3.14 创建项目
- 路径：`POST /project/create`
- 请求体：`{"name": "项目名称"}`（不可使用系统保留名「个人默认」/`__personal_default__`）
- 成功返回 `201`，`data` 含 `id`、`name`、`status`、`workspace_abs_path` 等。

### 3.15 查询项目列表
- 路径：`GET /project/list`
- 成功返回当前用户全部项目；首项为「个人默认」（`is_default: true`），含 `session_count`。

### 3.16 查询项目详情
- 路径：`GET /project/{project_id}`
- 成功返回项目详情及 `subdirs`（raw/processed/outputs/archive/sessions 是否存在）。

### 3.16.1 重命名项目
- 路径：`PUT /project/{project_id}`
- 请求体：`{"name": "新项目名称"}`（不可使用系统保留名；「个人默认」不可重命名）
- 成功返回更新后的项目详情。

### 3.16.2 查询项目目录树
- 路径：`GET /project/{project_id}/tree`
- 成功返回 `data.trees`，含 `raw`、`outputs`、`archive` 三节点的层级结构（复用 workspace-tree 格式）。

### 3.17 归档 / 恢复项目
- 路径：`POST /project/{project_id}/archive` | `POST /project/{project_id}/restore`
- 「个人默认」项目不可归档；其他项目归档时先将 `raw/`、`outputs/` 快照至 `archive/<YYYYMMDD-HHMMSS>/`（含 `manifest.json`），再设置 `status=archived`。
- 归档成功响应 `data.archive_snapshot_path` 为快照相对项目根的路径（如 `archive/20260705-153045`）。
- 归档后禁止新建会话、上传、分析。

### 3.18 项目级上传（预置 raw/，不直接进入分析）
- 路径：`POST /project/{project_id}/upload`
- Content-Type：`multipart/form-data`，字段 `file`
- 文件写入 `raw/`，并登记 `project_assets`（`asset_type=upload`）。
- **注意**：Agent 分析只读会话工作区。若需分析 raw/ 中文件，请使用 `POST /session/copy-from-project-raw` 或直接 `POST /session/upload-excel`。响应含 `deprecated: true` 与 `notice` 说明。

### 3.19 查询项目资产
- 路径：`GET /project/{project_id}/assets`
- 返回 `project_assets` 列表（含 upload / analysis_output）。
- 分析/模板执行结束后，关键产出会复制到 `outputs/<session_id>/` 并以 `outputs/...` 路径登记；同 `(project_id, relative_path)` 去重。

### 3.20 查询项目下会话
- 路径：`GET /project/{project_id}/sessions`
- 返回 `session_user WHERE project_id=?` 的 session_id / title / project_id 列表。
- 等价于 `GET /session/list?project_id={project_id}`（后者合并共享会话语义，推荐新接入使用 `/session/list`）。

### 3.21 平台用户管理（admin）
- `POST /admin/users` — 创建用户（body: username, phone, platform_role, status）
- `GET /admin/users?offset=&limit=` — 用户列表
- `GET /admin/users/{user_id}` — 用户详情
- `PUT /admin/users/{user_id}` — 更新用户

### 3.22 用户查找（邀请协作者）
- `GET /users/lookup?phone=13800138001` — 需 Bearer Token；精确匹配手机号，返回 `{ user_id, username, phone }`；未找到返回 404

### 3.23 项目成员管理
- `GET /project/{project_id}/members` — 项目成员均可读取成员列表
- `POST /project/{project_id}/members` — body: `user_id` 或 `phone`（二选一）, `role`, `permissions[]`
- `PUT /project/{project_id}/members/{user_id}`
- `DELETE /project/{project_id}/members/{user_id}`

项目列表/详情响应新增字段：`access`（owner | project_manager | member | admin）、`permissions`（权限码数组）、`is_shared`（是否为共享项目）

### 3.24 项目文件下载 / 删除
- `GET /project/{project_id}/download?relative_path=raw/data.csv` — 需 `data_download`
- `DELETE /project/{project_id}/assets` — body: `{ "relative_path": "..." }`，需 `data_delete`

### 3.25 占位任务 API
- `POST /project/{project_id}/tasks` — body: task_type, session_id?, payload?
- `GET /project/{project_id}/tasks`
- `GET /project/{project_id}/tasks/{task_id}`

完整 RBAC 说明见 [RBAC.md](RBAC.md)。

---

## 4. 数据存储与接口关系

### 4.1 工作区与执行 Runtime

- **项目目录布局**：`{TEMP_FOLDER}/workspaces/<user_id>/<project_id>/`，含子目录 `raw/`、`processed/`、`outputs/`、`archive/`、`sessions/`。
- **新会话目录**：`.../<project_id>/sessions/<session_id>/`（含「个人默认」项目）。
- **历史 legacy 目录**：`{TEMP_FOLDER}/workspaces/<user_id>/<session_id>/`（只读兼容；迁移见 `scripts/migrate-legacy-sessions.sh`）。
- **权威路径**：`session_user.workspace_abs_path` / `projects.workspace_abs_path`；`resolve_workspace_root(session_id)` 优先读 DB。
- **统一执行层**：[`src/runtime/`](../src/runtime/) 提供 `ensure_runtime(session_id)`，上层通过 `runtime.files.*` / `runtime.commands.run` 读写与执行，不再在各模块分散判断沙箱开关。
- **默认后端**：本地 Runtime（`CUBE_SANDBOX_ENABLED=0`），Worker 使用 **`RUNNER_PYTHON`** 指向的独立 conda 环境（如 `agentPlatform-runner`），与运行 FastAPI 的 `agentPlatform` 环境分离。
- **可选后端**：`CUBE_SANDBOX_ENABLED=1` 时经 `SandboxRuntimeAdapter` 走 Cube MicroVM；不可用时 factory 自动降级本地 Runtime。

- `session_user`：
  - 由 `/session/create` 创建并维护
  - 记录 `session_id -> user_id + project_id(可空) + title + workspace_abs_path`
  - `/session/save-title` 按 `session_id` 首次写入 `title`
  - `/session/list` 返回可访问会话（含 `project_id`、`access`）；可选 `?project_id=` 过滤
  - `/session/meta` 供前端同步会话所属项目
  - `/project/{id}/sessions` 等价于按项目过滤的会话列表（兼容保留）
- `projects` / `project_assets`：
  - 由 `/project/create` 等接口维护
  - `project_assets` 登记上传与分析产出（`asset_type`: `upload` | `analysis_output`）
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

登录成功后，从响应 JSON 的 `data.access_token` 取得 token，供后续请求使用。

```bash
curl -X POST "http://localhost:52716/auth/login-with-sms" \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "18395299120",
    "code": "123456"
  }'
```

### 5.3 获取当前用户

```bash
curl "http://localhost:52716/auth/me" \
  -H "Authorization: Bearer <access_token>"
```

### 5.4 修改用户姓名/昵称
```bash
curl -X POST "http://localhost:52716/auth/update-username" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "username": "张三"
  }'
```

### 5.5 创建项目
```bash
curl -X POST "http://localhost:52716/project/create" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{"name": "Q1 分析项目"}'
```

### 5.6 创建会话（需 project_id）
```bash
curl -X POST "http://localhost:52716/session/create" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{"project_id": 1}'
```

### 5.7 查询用户会话列表
```bash
curl "http://localhost:52716/session/list" \
  -H "Authorization: Bearer <access_token>"
```

### 5.8 保存会话标题
```bash
curl -X POST "http://localhost:52716/session/save-title" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "title": "Q1 销售分析"
  }'
```

### 5.9 上传数据文件
```bash
curl -X POST "http://localhost:52716/session/upload-excel" \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@./demo.xlsx" \
  -F "session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73"
```

### 5.9 查询快照
```bash
curl "http://localhost:52716/session/snapshot?session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73" \
  -H "Authorization: Bearer <access_token>"
```

### 5.10 查询会话工作区目录树
```bash
curl "http://localhost:52716/session/workspace-tree?session_id=9e9f3f2f-5978-4b31-a57f-95b0e6478b73" \
  -H "Authorization: Bearer <access_token>"
```

### 5.11 发起流式分析
```bash
curl -N -X POST "http://localhost:52716/run-analysis" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73",
    "input_data": "请对工作区目录下的数据做统计分析并给出结论。"
  }'
```

### 5.12 健康检查
```bash
curl http://localhost:52716/health
```

---

## 6. 说明与注意事项

- 上传接口在接口层校验扩展名，仅允许 Reader 可深度解析的 table/image/text 类型（见 §3.8）；大小限制为 `2048MB`，请同时确认反向代理（如 Nginx）配置一致。
- 除公开接口外，所有 API 需携带 `Authorization: Bearer <access_token>`；生产环境务必设置 `JWT_SECRET_KEY`（见 [AUTH.md](AUTH.md)）。
- SSE 是持续连接，前端需按流式协议处理 `data:` 行。
- 会话内容按“完整累计文本”落库，体量较大时可考虑后续改为增量片段存储策略。
- 短信验证码存储在服务内存中，服务重启后验证码会丢失；如需多实例部署，建议迁移到 Redis 等集中缓存。
- **执行 Runtime**：默认本地（`CUBE_SANDBOX_ENABLED=0`）。Worker 通过 `RUNNER_PYTHON`（建议 `agentPlatform-runner`）执行 Agent 生成的 Python，与 FastAPI 主环境隔离。详见 [`StartInstruction.md`](StartInstruction.md)。
- **Cube Sandbox（可选）**：`CUBE_SANDBOX_ENABLED=1` 时 factory 尝试沙箱后端；Cube 不可用时自动降级本地 Runtime。部署与排查见 [`Cubesandbox-agent-integration.md`](Cubesandbox-agent-integration.md)。
