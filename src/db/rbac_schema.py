# db/rbac_schema.py
# 角色与权限表结构定义

from typing import Any, List, Optional, TypedDict

TABLE_PROJECT_MEMBERS = "project_members"
TABLE_PROJECT_TASKS = "project_tasks"

# 平台角色
PLATFORM_ROLE_ADMIN = "admin"
PLATFORM_ROLE_USER = "user"
VALID_PLATFORM_ROLES = (PLATFORM_ROLE_ADMIN, PLATFORM_ROLE_USER)

# 用户状态
USER_STATUS_ACTIVE = "active"
USER_STATUS_BLOCKED = "blocked"
VALID_USER_STATUSES = (USER_STATUS_ACTIVE, USER_STATUS_BLOCKED)

# 项目成员角色
PROJECT_ROLE_MANAGER = "project_manager"
PROJECT_ROLE_MEMBER = "member"
VALID_PROJECT_ROLES = (PROJECT_ROLE_MANAGER, PROJECT_ROLE_MEMBER)

# 操作权限码
PERM_DATA_UPLOAD = "data_upload"
PERM_DATA_DELETE = "data_delete"
PERM_DATA_DOWNLOAD = "data_download"
PERM_DATA_ANNOTATE = "data_annotate"
PERM_DATA_REVIEW = "data_review"
PERM_ANALYSIS_CREATE = "analysis_create"
PERM_TRAINING_CREATE = "training_create"
PERM_MEMBER_MANAGE = "member_manage"

ALL_PERMISSIONS: tuple[str, ...] = (
    PERM_DATA_UPLOAD,
    PERM_DATA_DELETE,
    PERM_DATA_DOWNLOAD,
    PERM_DATA_ANNOTATE,
    PERM_DATA_REVIEW,
    PERM_ANALYSIS_CREATE,
    PERM_TRAINING_CREATE,
    PERM_MEMBER_MANAGE,
)

# 任务类型
TASK_TYPE_ANNOTATE = "annotate"
TASK_TYPE_REVIEW = "review"
TASK_TYPE_TRAINING = "training"
TASK_TYPE_ANALYSIS = "analysis"
VALID_TASK_TYPES = (TASK_TYPE_ANNOTATE, TASK_TYPE_REVIEW, TASK_TYPE_TRAINING, TASK_TYPE_ANALYSIS)

TASK_TYPE_TO_PERMISSION = {
    TASK_TYPE_ANNOTATE: PERM_DATA_ANNOTATE,
    TASK_TYPE_REVIEW: PERM_DATA_REVIEW,
    TASK_TYPE_TRAINING: PERM_TRAINING_CREATE,
    TASK_TYPE_ANALYSIS: PERM_ANALYSIS_CREATE,
}

TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_DONE = "done"
TASK_STATUS_FAILED = "failed"

# member 默认权限（仅下载）
DEFAULT_MEMBER_PERMISSIONS: List[str] = [PERM_DATA_DOWNLOAD]

PROJECT_MEMBER_COLUMNS = [
    "id",
    "project_id",
    "user_id",
    "role",
    "permissions",
    "created_at",
    "updated_at",
]

PROJECT_TASK_COLUMNS = [
    "id",
    "project_id",
    "session_id",
    "task_type",
    "status",
    "payload",
    "created_by",
    "created_at",
]


class ProjectMemberRow(TypedDict, total=False):
    id: int
    project_id: int
    user_id: int
    role: str
    permissions: Any
    created_at: Optional[str]
    updated_at: Optional[str]


class ProjectTaskRow(TypedDict, total=False):
    id: int
    project_id: int
    session_id: Optional[str]
    task_type: str
    status: str
    payload: Any
    created_by: int
    created_at: Optional[str]


USERS_ADD_PLATFORM_ROLE_DDL = """
ALTER TABLE users ADD COLUMN platform_role VARCHAR(32) NOT NULL DEFAULT 'user' COMMENT 'admin|user'
"""

USERS_ADD_STATUS_DDL = """
ALTER TABLE users ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active|blocked'
"""

PROJECT_MEMBERS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PROJECT_MEMBERS} (
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
"""

PROJECT_TASKS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PROJECT_TASKS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT NOT NULL COMMENT '所属项目ID',
    session_id VARCHAR(64) NULL COMMENT '关联会话ID',
    task_type VARCHAR(32) NOT NULL COMMENT 'annotate|review|training|analysis',
    status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT 'pending|running|done|failed',
    payload JSON NULL COMMENT '任务参数',
    created_by BIGINT NOT NULL COMMENT '创建者用户ID',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_project_id (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '项目任务登记表';
"""
