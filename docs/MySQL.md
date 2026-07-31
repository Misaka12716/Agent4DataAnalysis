# MySQL 使用指南

本文档用于本项目本地开发环境的 MySQL 连接、初始化与排查。

## 1. 当前项目默认配置（基于本地 Docker MySQL 8）

配置权威来源：仓库根目录 **`.env`** 中的 `MYSQL_*`；[`src/configs/config.py`](../src/configs/config.py) 仅 `os.getenv` 转发。

- `MYSQL_HOST=localhost`
- `MYSQL_PORT=3308`
- `MYSQL_USER=root`
- `MYSQL_PASSWORD=AgentPlatform2026!`（请写在 `.env`，勿提交到代码）
- `MYSQL_DB=agent_platform`
- `MYSQL_CHARSET=utf8mb4`

> 说明：以上为开发示例配置，生产环境请改为安全账号与强密码。

## 2. 配置和启动 MySQL

### 2.1 启动命令

当前使用的是 **Docker 单容器方式**，并且映射端口为 `3308:3306`，数据库初始化为 `agent_platform`。  
考虑到根分区 `/` 可能写满，推荐将 MySQL 数据目录直接挂载到 `/data1`。

```bash
# 1. 在 /data1 创建 MySQL 数据目录
sudo mkdir -p /data1/mysql/mysql8-agent-data
sudo chown -R 999:999 /data1/mysql/mysql8-agent-data

# 2. 运行 MySQL 容器（绑定挂载到 /data1）
sudo docker run -d \
  --name mysql8-agent \
  --restart unless-stopped \
  -p 3308:3306 \
  -v /data1/mysql/mysql8-agent-data:/var/lib/mysql \
  -e MYSQL_ROOT_PASSWORD=AgentPlatform2026! \
  -e MYSQL_DATABASE=agent_platform \
  mysql:8.0
```

> 说明：两种方式的容器配置完全一致，差别仅在于是否需要 `sudo`。

### 2.2 参数解释

- `--name mysql8-agent`：容器名，便于后续 `start/stop/logs/exec`
- `--restart unless-stopped`：系统重启后自动拉起
- `-p 3308:3306`：宿主机用 `3308`，容器内 MySQL 仍是 `3306`
- `-v /data1/mysql/mysql8-agent-data:/var/lib/mysql`：将数据落盘到 `/data1`，避免根分区写满
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

## 4. 初始化数据库和表结构

### 4.1 表清单

| 表名 | 用途 |
|------|------|
| `users` | 用户账号（含 `platform_role`、`status`） |
| `project_members` | 项目成员与权限 |
| `project_tasks` | 占位任务登记 |
| `projects` | 项目（含系统内置「个人默认」） |
| `project_assets` | 项目资产登记（上传 / 分析产出） |
| `session_user` | 会话 ↔ 用户 ↔ 项目 ↔ 工作区路径 |
| `session_content` | 会话流式内容版本 |
| `mental_health_templates` | 精神疾病分析模板（2.1.5，可选） |

Schema 源码：`src/db/models.py`、`src/db/project_schema.py`、`src/db/rbac_schema.py`、`src/db/template_schema.py`。

### 4.2 首次建库（空库）

```bash
sudo docker exec -it mysql8-agent mysql -u root -p
# 密码: AgentPlatform2026!
```

```sql
CREATE DATABASE IF NOT EXISTS agent_platform
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE agent_platform;
```

然后执行下方 **§4.3 全部 `CREATE TABLE` 语句**（跳过 `DROP TABLE` 部分）。

### 4.3 删表重建（开发环境数据可丢弃时推荐）

适用于：表结构已过时、或希望从零开始。**会删除库内全部业务数据。**

**方式 A：交互式**

```bash
sudo docker exec -it mysql8-agent mysql -u root -p
```

**方式 B：一条命令（宿主机）**

```bash
sudo docker exec -i mysql8-agent mysql -u root -p'AgentPlatform2026!' agent_platform <<'EOF'
-- 粘贴 §4.3 中从 DROP 到全部 CREATE 的 SQL
EOF
```

在 `USE agent_platform;` 之后按顺序执行：

```sql
USE agent_platform;

-- 1) 删表（先子表后主表）
DROP TABLE IF EXISTS project_assets;
DROP TABLE IF EXISTS session_content;
DROP TABLE IF EXISTS session_user;
DROP TABLE IF EXISTS projects;
DROP TABLE IF EXISTS mental_health_templates;
DROP TABLE IF EXISTS users;

-- 2) 建表
CREATE TABLE users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(128) NOT NULL UNIQUE COMMENT '用户名',
    phone VARCHAR(32) NULL COMMENT '手机号',
    email VARCHAR(256) NULL COMMENT '邮箱',
    password_hash VARCHAR(256) NOT NULL COMMENT '密码哈希',
    platform_role VARCHAR(32) NOT NULL DEFAULT 'user' COMMENT 'admin|user',
    status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active|blocked',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '用户基础信息表';

CREATE TABLE projects (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '所属用户ID',
    name VARCHAR(255) NOT NULL COMMENT '项目名称',
    status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active|archived',
    workspace_abs_path VARCHAR(512) NOT NULL COMMENT '项目工作区绝对路径',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '项目表';

CREATE TABLE session_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL UNIQUE COMMENT '会话ID',
    user_id BIGINT NOT NULL COMMENT '用户ID',
    project_id BIGINT NULL COMMENT '所属项目ID',
    title VARCHAR(255) NULL COMMENT '会话标题',
    workspace_abs_path VARCHAR(512) NOT NULL COMMENT '工作区绝对路径',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_project_id (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '会话-用户关联表';

CREATE TABLE session_content (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL COMMENT '会话ID',
    version INT NOT NULL DEFAULT 0 COMMENT '版本号/片段序号',
    content LONGTEXT NOT NULL COMMENT '完整累计内容',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_session_version (session_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '会话内容存储表';

CREATE TABLE project_assets (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT NOT NULL COMMENT '所属项目ID',
    session_id VARCHAR(64) NULL COMMENT '关联会话ID',
    asset_type VARCHAR(32) NOT NULL COMMENT 'upload|analysis_output',
    relative_path VARCHAR(512) NOT NULL COMMENT '相对项目根目录的路径',
    original_filename VARCHAR(255) NULL COMMENT '原始文件名',
    file_category VARCHAR(32) NULL COMMENT 'table|image|text|other',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_project_id (project_id),
    INDEX idx_session_id (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '项目资产登记表';

CREATE TABLE mental_health_templates (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    template_name VARCHAR(256) NOT NULL UNIQUE COMMENT '模板名称',
    disease_type VARCHAR(64) NOT NULL COMMENT '专病类型',
    scales JSON NOT NULL COMMENT '症状量表清单',
    analysis_steps JSON NOT NULL COMMENT '分析步骤定义',
    report_structure JSON NOT NULL COMMENT '报告章节结构',
    version VARCHAR(16) NOT NULL DEFAULT '1.0.0' COMMENT '语义版本号',
    version_history JSON DEFAULT NULL COMMENT '历史版本快照',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '精神疾病分析模板表';

CREATE TABLE project_members (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT NOT NULL COMMENT '所属项目ID',
    user_id BIGINT NOT NULL COMMENT '成员用户ID',
    role VARCHAR(32) NOT NULL DEFAULT 'member' COMMENT 'project_manager|member',
    permissions JSON NOT NULL COMMENT '权限码数组',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_project_user (project_id, user_id),
    INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '项目成员表';

CREATE TABLE project_tasks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT NOT NULL COMMENT '所属项目ID',
    session_id VARCHAR(64) NULL COMMENT '关联会话ID',
    task_type VARCHAR(32) NOT NULL COMMENT 'annotate|review|training|analysis',
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    payload JSON NULL,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_project_id (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '项目任务登记表';
```

**已有库增量迁移**（后端启动时会自动执行，也可手工运行）：

```sql
ALTER TABLE users ADD COLUMN platform_role VARCHAR(32) NOT NULL DEFAULT 'user';
ALTER TABLE users ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active';
-- project_members / project_tasks 见 src/db/rbac_schema.py
```

**设置初始管理员**：

```sql
UPDATE users SET platform_role = 'admin' WHERE phone = '你的手机号';
```

详见 [RBAC.md](RBAC.md)。

### 4.4 可选：清空磁盘工作区

删表重建后，旧会话文件仍可能留在 `tmp/workspaces/`。若一并清理：

```bash
rm -rf /data1/pjw/AgentPlatform/tmp/workspaces/*
```

路径以 `src/configs/config.py` 中 `TEMP_FOLDER` 为准。

### 4.5 重建后的应用行为

1. **重启后端**（若已在运行）。
2. **重新登录 / 注册**（`users` 表已空）。
3. 用户首次访问 `/project/list` 或 `/session/create` 时，后端会自动创建 **「个人默认」** 项目（库内名 `__personal_default__`，界面显示「个人默认」），无需手工 INSERT。
4. 模板数据可选导入：`POST /template/import`，或运行 `bash scripts/init-platform.sh`。

**「个人默认」项目说明**：

- 不可手动创建同名项目，不可归档。
- 其下新建会话使用旧路径布局：`workspaces/<user_id>/<session_id>/`。
- 用户自建项目下新会话使用：`workspaces/<user_id>/<project_id>/sessions/<session_id>/`。

## 5. 校验是否创建成功

```sql
USE agent_platform;
SHOW TABLES;

DESC users;
DESC projects;
DESC session_user;
DESC session_content;
DESC project_assets;
DESC mental_health_templates;
```

期望看到 6 张表。

## 6. 常用排查命令

查看当前连接和库：

```sql
SELECT DATABASE();
SELECT USER();
```

查看 MySQL 是否监听 3308 端口（Linux）：

```bash
ss -lntp | rg 3308
```

查看容器状态：

```bash
sudo docker ps -a --filter name=mysql8-agent
sudo docker inspect mysql8-agent | rg -n "3308|MYSQL_DATABASE|MYSQL_ROOT_PASSWORD"
```

容器已存在时，常用启停：

```bash
sudo docker start mysql8-agent
sudo docker stop mysql8-agent
```

### 6.1 根分区写满（1114）时迁移到 `/data1`

当出现 `ERROR 1114 (HY000): The table 'xxx' is full` 且 `df -h` 显示 `/` 已 100% 时，可按以下步骤迁移。

```bash
# 0. 停容器（避免迁移期间写入）
sudo docker stop mysql8-agent

# 1. 在 /data1 创建新数据目录
sudo mkdir -p /data1/mysql/mysql8-agent-data
sudo chown -R 999:999 /data1/mysql/mysql8-agent-data

# 2. 若旧容器已存在，把旧数据目录拷贝到 /data1（保留权限/时间）
# 先查看旧挂载源路径（Mounts.Source）
sudo docker inspect mysql8-agent | rg -n "Source|Destination"
# 假设旧路径是 /var/lib/docker/volumes/mysql8-agent-data/_data
sudo rsync -aHAX /var/lib/docker/volumes/mysql8-agent-data/_data/ /data1/mysql/mysql8-agent-data/

# 3. 删除旧容器（仅删除容器，不删 /data1 目录里的数据）
sudo docker rm mysql8-agent

# 4. 使用 /data1 目录重新创建容器
sudo docker run -d \
  --name mysql8-agent \
  --restart unless-stopped \
  -p 3308:3306 \
  -v /data1/mysql/mysql8-agent-data:/var/lib/mysql \
  -e MYSQL_ROOT_PASSWORD=AgentPlatform2026! \
  -e MYSQL_DATABASE=agent_platform \
  mysql:8.0

# 5. 校验
df -h
sudo docker logs mysql8-agent | rg -n "ready for connections"
sudo docker exec -it mysql8-agent mysql -u root -p -e "SHOW DATABASES;"
```

> 说明：如旧数据不需要保留，可跳过 `rsync`，直接新建空库。

## 7. 与代码的对应关系

- 表结构参考：
  - `src/db/models.py` — `users`、`session_user`、`session_content`
  - `src/db/project_schema.py` — `projects`、`project_assets`
  - `src/db/template_schema.py` — `mental_health_templates`
- 运行时配置读取：仓库根 `.env` 的 `MYSQL_*`（经 `src/configs/config.py` 转发）

Cube Sandbox 与工作区镜像语义见 [Cubesandbox-agent-integration.md](./Cubesandbox-agent-integration.md)。

请确保 `.env` 与容器参数一致：

- `MYSQL_HOST=localhost`
- `MYSQL_PORT=3308`
- `MYSQL_USER=root`
- `MYSQL_PASSWORD=AgentPlatform2026!`
- `MYSQL_DB=agent_platform`

如果你修改了库名、端口或账号密码，请同步更新 `.env` 后重启后端服务。

## 8. 查询完整对话记录 / 工作区路径

以下查询都以 `session_id` 为主键线索。

### 8.1 查询某个会话对应的工作区路径

```sql
SELECT
  session_id,
  user_id,
  project_id,
  title,
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
  su.project_id,
  su.title,
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

### 9.1 `users`（用户基础信息）

用途：存放平台用户账号信息；短信登录后签发 JWT，`user_id` 由 token 解析，不再由客户端明文传参。

主要字段：`id`、`username`（唯一）、`phone`、`email`、`password_hash`、`created_at` / `updated_at`。

### 9.2 `projects`（项目）

用途：多项目管理；每个用户有一个系统内置 **「个人默认」** 项目（库内 `name = '__personal_default__'`）。

主要字段：

- `id`：项目 ID
- `user_id`：所属用户
- `name`：项目名（内置默认项在 API 显示为「个人默认」）
- `status`：`active` | `archived`（个人默认不可归档）
- `workspace_abs_path`：项目根目录，形如 `.../workspaces/<user_id>/<project_id>/`

### 9.3 `project_assets`（项目资产）

用途：登记上传到 `raw/` 或会话/分析产生的文件路径。

主要字段：`project_id`、`session_id`（可空）、`asset_type`（`upload` | `analysis_output`）、`relative_path`、`original_filename`、`file_category`。

### 9.4 `session_user`（会话与用户、项目、工作区映射）

用途：把 `session_id` 映射到 `user_id`、`project_id` 与会话工作区绝对路径。

主要字段：

- `session_id`：会话 ID（唯一）
- `user_id`：用户 ID
- `project_id`：所属项目（可为空；正常运行时由后端写入）
- `title`：会话标题（可空，首次写入后不覆盖）
- `workspace_abs_path`：会话工作区绝对路径

工作区路径布局：

| 场景 | 路径 |
|------|------|
| 「个人默认」项目下的会话 | `{TEMP}/workspaces/<user_id>/<session_id>/` |
| 用户自建项目下的会话 | `{TEMP}/workspaces/<user_id>/<project_id>/sessions/<session_id>/` |

启用 Cube Sandbox 时，DB 仍存本地镜像路径；`.cube_sandbox_meta.json` 位于工作区目录（不入库）。

### 9.5 `session_content`（会话流式内容版本表）

用途：存放会话的流式输出内容，按 `version` 递增，用于断线重连和历史回放。

主要字段：`session_id`、`version`（递增）、`content`（LONGTEXT，多为多行 JSON 文本）、`created_at`。

唯一键：`uk_session_version(session_id, version)`。

`content` 示例片段：

```text
{"type":"user_input","content":"请分析数据","timestamp":"2026-04-14 10:00:00"}
{"type":"report_chunk","content":"第一部分结论..."}
{"type":"streaming_ended","message":"分析任务流式输出结束","timestamp":"2026-04-14 10:00:12"}
```

### 9.6 `mental_health_templates`（分析模板，可选）

用途：2.1.5 精神疾病定量分析模板；可由 `POST /template/import` 从 `knowledge/templates/` 导入。

主要字段：`template_name`、`disease_type`、`scales` / `analysis_steps` / `report_structure`（JSON）、`version`、`version_history`。