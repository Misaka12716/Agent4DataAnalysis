# 角色与权限管理（RBAC）

本文档说明 AgentPlatform 的平台级角色、项目级成员权限及 API 使用方式。

## 1. 角色体系

### 1.1 平台角色（`users.platform_role`）

| 角色 | 说明 |
|------|------|
| `admin` | 平台管理员：用户 CRUD、任意项目成员管理、绕过项目权限检查 |
| `user` | 普通用户（默认） |

### 1.2 项目角色（`project_members.role`）

| 角色 | 说明 |
|------|------|
| `project_manager` | 项目负责人：项目内全部操作 + 成员管理 |
| `member` | 普通成员：按分配的操作权限码执行 |

项目所有者（`projects.user_id`）自动拥有全部项目权限，无需写入 `project_members`。

## 2. 操作权限码

| 权限码 | 说明 |
|--------|------|
| `data_upload` | 数据上传 |
| `data_delete` | 数据删除 |
| `data_download` | 数据下载 |
| `data_annotate` | 数据标注 |
| `data_review` | 数据审核 |
| `analysis_create` | 统计分析任务创建 |
| `training_create` | 模型训练任务创建 |
| `member_manage` | 项目成员管理 |

## 3. 默认权限矩阵

| 角色 | upload | delete | download | annotate | review | analysis | training | member_manage |
|------|--------|--------|----------|----------|--------|----------|----------|---------------|
| admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| project_manager | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| member（默认） | — | — | ✓ | — | — | — | — | — |

添加 `member` 时可勾选权限；设为 `project_manager` 则自动拥有全部项目权限。

## 4. 访问规则

- **项目读**（list/detail/tree/assets/sessions/tasks）：所有者 OR 成员 OR admin
- **项目写**（upload/delete 等）：所有者 OR（成员 + 对应权限）OR admin
- **项目生命周期**（重命名/归档/恢复）：admin OR 项目所有者 OR `project_manager`
- **会话访问**：会话创建者 OR 会话所属项目的成员/admin
- **会话列表**（`GET /session/list`）：自己创建的会话 + 可访问项目内的全部会话（含他人创建）；响应含 `access`（`owner` | `shared`）与可选 `project_id`
- **项目列表**（`GET /project/list`）：普通用户为 owned + member 项目；平台 admin 返回全部项目；每项含 `access`、`permissions`、`is_shared`
- **项目详情**（`GET /project/{id}`）：响应含当前用户的 `access`、`permissions`、`is_shared`
- **成员列表**（`GET /project/{id}/members`）：任意项目成员可读；添加/更新/移除需成员管理权限
- **成员管理**（写操作）：admin OR 项目所有者 OR project_manager OR 带 `member_manage` 的 member
- **个人默认项目**：不支持成员管理（避免误共享私人数据）
- **归档项目**：仍禁止写操作（code=8）

## 4.1 数据隔离层级

| 层级 | 机制 | 说明 |
|------|------|------|
| 用户/平台 | JWT + `platform_role` + 封禁 | 未登录或封禁用户无法访问 API |
| 项目 | 所有者 / 成员角色 / 权限码 | 非成员无法读取项目数据、资产与分析产出 |
| 会话 | 创建者或项目成员 | 会话快照、工作区、流式分析均校验项目权限 |
| 文件 | 路径 traversal 防护 + 权限码 | 下载/删除需对应 `data_download` / `data_delete` |

**当前边界**：隔离止于项目级；CSV/Excel 中的 `patient_id` 列尚未建立独立患者实体或行级 ACL。患者级子集授权为后续规划项。

前端 Streamlit 页面通过 `/auth/me` 缓存的 `permissions_summary` 按权限隐藏/禁用上传、分析、归档等操作；**后端 API 校验仍是安全边界**。

**产品前端对接**：详见 [`2.1.1FrontendIntegrationGuide.md`](2.1.1FrontendIntegrationGuide.md)（项目/会话模型、页面流程、权限缓存、API 速查）。

## 5. HTTP 错误码

| HTTP | code | 含义 |
|------|------|------|
| 401 | 6 | 未登录或 token 无效 |
| 403 | 3 | 用户已被封禁 |
| 403 | 7 | 资源访问被拒绝 |
| 403 | 8 | 项目已归档 |
| 403 | 9 | 权限不足 |

## 6. API 清单

### 6.1 平台管理（admin）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/admin/users` | 创建用户 |
| GET | `/admin/users` | 用户列表 |
| GET | `/admin/users/{id}` | 用户详情 |
| PUT | `/admin/users/{id}` | 更新角色/状态/用户名 |

### 6.2 项目成员

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/users/lookup?phone=` | 按手机号精确查找用户（需登录，用于邀请协作者） |
| GET | `/project/{id}/members` | 成员列表（项目成员可读） |
| POST | `/project/{id}/members` | 添加成员（`user_id` 或 `phone` 二选一） |
| PUT | `/project/{id}/members/{user_id}` | 更新成员 |
| DELETE | `/project/{id}/members/{user_id}` | 移除成员 |

### 6.3 文件与任务

| 方法 | 路径 | 权限 |
|------|------|------|
| GET | `/project/{id}/download?relative_path=` | `data_download` |
| DELETE | `/project/{id}/assets` | `data_delete` |
| POST | `/project/{id}/tasks` | 按 task_type 映射 |
| GET | `/project/{id}/tasks` | 项目读 |
| GET | `/project/{id}/tasks/{task_id}` | 项目读 |

`task_type`：`annotate` | `review` | `training` | `analysis`

## 7. 初始管理员

**方式 A**：环境变量（首次登录时自动提升）

```bash
export INITIAL_ADMIN_PHONE=18395299120
```

**方式 B**：SQL

```sql
UPDATE users SET platform_role = 'admin' WHERE phone = '18395299120';
```

## 8. 前端管理页

Streamlit 多页应用（需先登录）：

- `frontend/pages/admin_users.py` — 用户管理（admin）
- `frontend/pages/project_members.py` — 项目成员与权限（需 `member_manage` 方可编辑）
- `frontend/pages/project_workspace.py` — 项目工作区（按权限控制上传/归档/重命名）
- `frontend/app.py` — 主控制台（侧栏标注共享会话，按权限控制上传与分析）

前端权限辅助函数见 `src/frontend/page_utils.py`（`project_has_permission`、`can_upload`、`can_analyze`、`can_manage_project` 等）。

## 9. 源码索引

- Schema：`src/db/rbac_schema.py`
- Store：`src/db/rbac_store.py`
- 权限逻辑：`src/backend/permission_service.py`
- 访问校验：`src/backend/project_auth.py`
- 会话列表：`src/db/session_store.py`（`get_accessible_sessions`）
- 路由：`src/backend/admin_routes.py`、`src/backend/member_routes.py`
