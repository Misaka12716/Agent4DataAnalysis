# MySQL 使用指南

本文档用于本项目本地开发环境的 MySQL 连接、初始化与排查。

## 1. 当前项目默认配置（基于本地 Docker MySQL 8）

配置来源：`src/configs/config.py`

- `MYSQL_HOST=localhost`
- `MYSQL_PORT=3307`
- `MYSQL_USER=root`
- `MYSQL_PASSWORD=AgentPlatform2026!`
- `MYSQL_DB=agent_platform`
- `MYSQL_CHARSET=utf8mb4`

> 说明：以上为开发示例配置，生产环境请改为安全账号与强密码。

## 2. 配置和启动 MySQL

### 2.1 启动命令

当前使用的是 **Docker 单容器方式**，并且映射端口为 `3307:3306`，数据库初始化为 `agent_platform`。

```bash
# 1. 创建一个 Docker 卷，用于永久存放 MySQL 数据
docker volume create mysql8-agent-data

# 2. 运行 MySQL 容器
sudo docker run -d \
  --name mysql8-agent \
  --restart unless-stopped \
  -p 3307:3306 \
  -v mysql8-agent-data:/var/lib/mysql \
  -e MYSQL_ROOT_PASSWORD=AgentPlatform2026! \
  -e MYSQL_DATABASE=agent_platform \
  mysql:8.0
```

> 说明：两种方式的容器配置完全一致，差别仅在于是否需要 `sudo`。

### 2.2 参数解释

- `--name mysql8-agent`：容器名，便于后续 `start/stop/logs/exec`
- `--restart unless-stopped`：系统重启后自动拉起
- `-p 3307:3306`：宿主机用 `3307`，容器内 MySQL 仍是 `3306`
- `-v mysql8-agent-data:/var/lib/mysql`：使用 Docker Volume 持久化数据
- `-e MYSQL_ROOT_PASSWORD=...`：初始化 root 密码
- `-e MYSQL_DATABASE=agent_platform`：首次启动自动创建数据库

### 2.3 启动后快速校验

```bash
sudo docker ps --filter name=mysql8-agent
sudo docker logs mysql8-agent
```

看到 `ready for connections` 可认为启动成功。

## 3. 连接数据库

```bash
sudo docker exec -it mysql8-agent mysql -u root -p
# 输入密码: AgentPlatform2026!
```

输入密码后，选择数据库：

```sql
USE agent_platform;
```

## 4. 初始化数据库和核心表

系统核心使用三张表：

- `users`
- `session_user`
- `session_content`

可直接执行以下 SQL：

```sql
CREATE DATABASE IF NOT EXISTS agent_platform
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE agent_platform;

CREATE TABLE IF NOT EXISTS users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(128) NOT NULL UNIQUE COMMENT '用户名',
    phone VARCHAR(32) NULL COMMENT '手机号',
    email VARCHAR(256) NULL COMMENT '邮箱',
    password_hash VARCHAR(256) NOT NULL COMMENT '密码哈希',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '用户基础信息表';

CREATE TABLE IF NOT EXISTS session_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL UNIQUE COMMENT '会话ID',
    user_id BIGINT NOT NULL COMMENT '用户ID',
    title VARCHAR(255) NULL COMMENT '会话标题',
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

## 5. 校验是否创建成功

```sql
SHOW TABLES;
DESC users;
DESC session_user;
DESC session_content;
```

## 6. 常用排查命令

查看当前连接和库：

```sql
SELECT DATABASE();
SELECT USER();
```

查看 MySQL 是否监听 3307 端口（Linux）：

```bash
ss -lntp | rg 3307
```

查看容器状态：

```bash
sudo docker ps -a --filter name=mysql8-agent
sudo docker inspect mysql8-agent | rg -n "3307|MYSQL_DATABASE|MYSQL_ROOT_PASSWORD"
```

容器已存在时，常用启停：

```bash
sudo docker start mysql8-agent
sudo docker stop mysql8-agent
```

## 7. 与代码的对应关系

- 表结构参考：`src/db/models.py`
  - `SESSION_USER_TABLE_DDL`
  - `SESSION_CONTENT_TABLE_DDL`
- 运行时配置读取：`src/utils/config.py`

请确保代码配置与容器参数一致：

- `MYSQL_HOST=localhost`
- `MYSQL_PORT=3307`
- `MYSQL_USER=root`
- `MYSQL_PASSWORD=AgentPlatform2026!`
- `MYSQL_DB=agent_platform`

如果你修改了库名、端口或账号密码，请同步更新配置后重启后端服务。

## 8. 查询完整对话记录 / 工作区路径

以下查询都以 `session_id` 为主键线索。

### 8.1 查询某个会话对应的工作区路径

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

### 8.2 查询某个会话的“完整累计内容”

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

### 8.3 查询某个会话的历史版本（按时间线）

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

### 8.4 一条 SQL 同时取“工作区路径 + 最新完整内容”

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

## 9. 各表存放内容与数据格式

当前项目核心使用三张表（其中 `users` 为可扩展用户表，`session_*` 为会话主链路核心）。

### 9.1 `users`（用户基础信息）

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
phone=18395299120
email=alice@example.com
password_hash=$2b$12$...
```

### 9.2 `session_user`（会话与用户、工作区映射）

用途：把 `session_id` 映射到 `user_id` 与会话工作区绝对路径。  
这是定位“某次对话对应哪个工作目录”的关键表。
后端 `GET /session/list` 接口会直接读取本表的 `session_id/title` 并通过 `sessions` 字段返回。

主要字段与格式：

- `id`：`BIGINT`，自增主键
- `session_id`：`VARCHAR(64)`，会话 ID（唯一）
- `user_id`：`BIGINT`，用户 ID
- `title`：`VARCHAR(255)`，会话标题（可空，首次写入后不覆盖）
- `workspace_abs_path`：`VARCHAR(512)`，会话工作区绝对路径
- `created_at` / `updated_at`：`TIMESTAMP`

示例（逻辑）：

```text
session_id=1d9c5c6e-3b2a-4c49-8e4f-5f0c6f91c9d2
user_id=0
title=Q1 销售数据分析
workspace_abs_path=/data1/pjw/AgentPlatform/tmp/workspaces/1d9c5c6e-3b2a-4c49-8e4f-5f0c6f91c9d2
```

### 9.3 `session_content`（会话流式内容版本表）

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