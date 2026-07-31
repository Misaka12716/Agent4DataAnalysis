# 用户鉴权说明

本文档描述 AgentPlatform 后端的 JWT Bearer Token 鉴权机制，与 [BackendAPI.md](BackendAPI.md) 配合阅读。

---

## 1. 设计目标

- 消除客户端明文传 `user_id` 导致的 IDOR 风险
- 确保 `session_id` 操作只能由会话所有者执行
- 与现有短信登录流程兼容，不引入额外 session 存储（无 Redis）

---

## 2. 认证流程

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant MySQL

    Client->>API: POST /auth/send-sms-code {phone}
    API->>Client: 验证码已发送
    Client->>API: POST /auth/login-with-sms {phone, code}
    API->>MySQL: 查/建 users
    API->>Client: access_token + user info
    Client->>API: Authorization Bearer token
    API->>API: 解析 JWT，得到 user_id
    API->>MySQL: 业务操作 + session 归属校验
```

---

## 3. JWT 配置

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `JWT_SECRET_KEY` | HS256 签名密钥 | 空（开发环境自动生成临时密钥并打 warning） |
| `JWT_EXPIRE_HOURS` | token 有效期（小时） | `168`（7 天） |

代码位置：

- 配置：[`src/configs/config.py`](../src/configs/config.py)
- 签发/解析：[`src/backend/jwt_auth.py`](../src/backend/jwt_auth.py)
- 会话归属：[`src/backend/project_auth.py`](../src/backend/project_auth.py)

**生产环境必须设置 `JWT_SECRET_KEY`**，且不要使用代码仓库中的默认值。

---

## 4. Token 载荷

登录成功后签发的 JWT 包含：

| 字段 | 说明 |
|------|------|
| `sub` | 用户 ID 字符串 |
| `user_id` | 用户 ID（整数） |
| `username` | 用户名 |
| `phone` | 手机号 |
| `iat` | 签发时间 |
| `exp` | 过期时间 |

---

## 5. 接口鉴权矩阵

### 公开（无需 token）

- `GET /health`
- `POST /auth/send-sms-code`
- `POST /auth/login-with-sms`

### 需登录（Bearer Token）

| 接口 | 额外校验 |
|------|---------|
| `GET /auth/me` | — |
| `POST /auth/update-username` | 仅修改 token 对应用户 |
| `POST /session/create` | 会话归属当前用户 |
| `GET /session/list` | 仅返回当前用户会话 |
| `POST /session/save-title` | session 归属 |
| `POST /session/upload-excel` | session 归属 |
| `GET /session/snapshot` | session 归属 |
| `GET /session/workspace-tree` | session 归属 |
| `POST /run-analysis` | session 归属 |
| `POST /run-analysis/reconnect` | session 归属 |

---

## 6. 错误码

| HTTP | code | msg | 场景 |
|------|------|-----|------|
| 401 | 6 | unauthorized | 未携带 token、格式错误、签名无效或已过期 |
| 403 | 7 | forbidden: session access denied | token 有效，但 `session_user.user_id` 与当前用户不一致 |

---

## 7. 客户端集成

### 登录

```bash
curl -X POST "http://localhost:52716/auth/login-with-sms" \
  -H "Content-Type: application/json" \
  -d '{"phone":"13800138000","code":"123456"}'
```

从响应 `data.access_token` 保存 token。

### 后续请求

```bash
curl "http://localhost:52716/session/list" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
```

Streamlit 联调前端（[`src/frontend/frontend.py`](../src/frontend/frontend.py)）在 `st.session_state["access_token"]` 中保存 token，并通过 `_auth_headers()` 统一附加请求头。

---

## 8. Breaking Changes（相对旧版 API）

1. 登录响应新增 `access_token`、`token_type`、`expires_in`
2. `POST /session/create` 不再接受 body 中的 `user_id`
3. `GET /session/list` 不再接受 query `user_id`
4. `POST /auth/update-username` 不再接受 body 中的 `user_id`
5. 所有 session 接口：仅凭 `session_id` 无法访问他人资源

---

## 9. 后续可扩展

- **双 Token 鉴权（Access + Refresh）**：详见 [AUTH_DUAL_TOKEN.md](AUTH_DUAL_TOKEN.md)
- 短信发送速率限制
- `users.is_blocked` 字段与 DDL 对齐
