# MySQL 使用指南

本文档用于本项目本地开发环境的 MySQL 连接、初始化与排查。

## 1. 当前项目默认配置

配置来源：`src/utils/config.py`

- `MYSQL_HOST=localhost`
- `MYSQL_PORT=3306`
- `MYSQL_USER=root`
- `MYSQL_PASSWORD=88888888`
- `MYSQL_DB=agent_platform`
- `MYSQL_CHARSET=utf8mb4`

> 说明：以上为开发示例配置，生产环境请改为安全账号与强密码。

## 2. 连接数据库

```bash
mysql -h localhost -P 3306 -u root -p
```

输入密码后，选择数据库：

```sql
USE agent_platform;
```

## 3. 初始化数据库和核心表

系统至少依赖两张会话相关表：

- `session_user`
- `session_content`

可直接执行以下 SQL：

```sql
CREATE DATABASE IF NOT EXISTS agent_platform
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE agent_platform;

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

## 4. 校验是否创建成功

```sql
SHOW TABLES;
DESC session_user;
DESC session_content;
```

## 5. 常用排查命令

查看当前连接和库：

```sql
SELECT DATABASE();
SELECT USER();
```

查看 MySQL 是否监听 3306 端口（Linux）：

```bash
ss -lntp | rg 3306
```

## 6. 与代码的对应关系

- 表结构参考：`src/db/models.py`
  - `SESSION_USER_TABLE_DDL`
  - `SESSION_CONTENT_TABLE_DDL`
- 运行时配置读取：`src/utils/config.py`

如果你修改了库名、端口或账号密码，请同步更新配置后重启后端服务。

## 7. 查询完整对话记录 / 工作区路径

以下查询都以 `session_id` 为主键线索。

### 7.1 查询某个会话对应的工作区路径

```sql
SELECT
  session_id,
  user_id,
  workspace_abs_path,
  created_at,
  updated_at
FROM session_user
WHERE session_id = '你的_session_id';
```

### 7.2 查询某个会话的“完整累计内容”

`session_content` 采用版本递增写入；通常取最大 `version` 即当前完整内容。

```sql
SELECT
  session_id,
  version,
  content,
  created_at
FROM session_content
WHERE session_id = '你的_session_id'
ORDER BY version DESC
LIMIT 1;
```

### 7.3 查询某个会话的历史版本（按时间线）

```sql
SELECT
  session_id,
  version,
  created_at
FROM session_content
WHERE session_id = '你的_session_id'
ORDER BY version ASC;
```

如需查看某个历史版本的具体内容：

```sql
SELECT
  session_id,
  version,
  content,
  created_at
FROM session_content
WHERE session_id = '你的_session_id'
  AND version = 123;
```

### 7.4 一条 SQL 同时取“工作区路径 + 最新完整内容”

```sql
SELECT
  su.session_id,
  su.user_id,
  su.workspace_abs_path,
  sc.version,
  sc.content,
  sc.created_at AS content_created_at
FROM session_user su
LEFT JOIN session_content sc
  ON sc.session_id = su.session_id
  AND sc.version = (
    SELECT MAX(s2.version)
    FROM session_content s2
    WHERE s2.session_id = su.session_id
  )
WHERE su.session_id = '你的_session_id';
```

> 说明：如果会话刚创建、尚未产生流式内容，`sc.*` 可能为 `NULL`，这是正常现象。

## 8. 各表存放内容与数据格式

当前项目核心使用三张表（其中 `users` 为可扩展用户表，`session_*` 为会话主链路核心）。

### 8.1 `users`（用户基础信息）

用途：存放平台用户账号信息，便于后续做登录、权限、审计扩展。

主要字段与格式：

- `id`：`BIGINT`，自增主键
- `username`：`VARCHAR(128)`，用户名（唯一）
- `phone`：`VARCHAR(32)`，手机号，可空
- `email`：`VARCHAR(256)`，邮箱，可空
- `password_hash`：`VARCHAR(256)`，密码哈希（不存明文）
- `created_at` / `updated_at`：`TIMESTAMP`

示例（逻辑）：

```text
id=1
username=alice
phone=13800138000
email=alice@example.com
password_hash=$2b$12$...
```

### 8.2 `session_user`（会话与用户、工作区映射）

用途：把 `session_id` 映射到 `user_id` 与会话工作区绝对路径。  
这是定位“某次对话对应哪个工作目录”的关键表。

主要字段与格式：

- `id`：`BIGINT`，自增主键
- `session_id`：`VARCHAR(64)`，会话 ID（唯一）
- `user_id`：`BIGINT`，用户 ID
- `workspace_abs_path`：`VARCHAR(512)`，会话工作区绝对路径
- `created_at` / `updated_at`：`TIMESTAMP`

示例（逻辑）：

```text
session_id=1d9c5c6e-3b2a-4c49-8e4f-5f0c6f91c9d2
user_id=0
workspace_abs_path=/data/agent_platform/tmp/workspaces/1d9c5c6e-3b2a-4c49-8e4f-5f0c6f91c9d2
```

### 8.3 `session_content`（会话流式内容版本表）

用途：存放会话的流式输出内容，按 `version` 递增。  
上层逻辑一般将每次事件 JSON 追加到“完整累计内容”，用于断线重连和历史回放。

主要字段与格式：

- `id`：`BIGINT`，自增主键
- `session_id`：`VARCHAR(64)`，会话 ID
- `version`：`INT`，版本号/片段序号（递增）
- `content`：`LONGTEXT`，文本内容（通常是累计后的多行 JSON 文本）
- `created_at`：`TIMESTAMP`

约束：

- 唯一键 `uk_session_version(session_id, version)`

`content` 常见格式说明：

- 每次流式事件会先被序列化为 JSON 字符串
- 再以换行分隔追加到该会话累计内容中
- 因此 `content` 常呈现为“多行 JSON 文本”而非单个 JSON 数组

示例（片段）：

```text
{"type":"orchestrator","data":{"next":"planner","reason":"...","timestamp":"2026-04-14 10:00:00"}}
{"type":"planner","data":{"type":"stage_result","data":{"需求解析":"...","步骤分解":"..."}}}
{"type":"report_chunk","content":"第一部分结论..."}
{"type":"streaming_ended","message":"分析任务流式输出结束","timestamp":"2026-04-14 10:00:12"}
```