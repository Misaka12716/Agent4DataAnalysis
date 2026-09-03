#!/usr/bin/env python3
"""平台核心初始化：建表、默认用户、默认项目、可选演示数据。"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.current_user import DEFAULT_USER_ID  # noqa: E402
from backend.project_service import ProjectService  # noqa: E402
from db.models import (  # noqa: E402
    SESSION_CONTENT_TABLE_DDL,
    SESSION_USER_TABLE_DDL,
    TABLE_SESSION_CONTENT,
    TABLE_SESSION_USER,
    TABLE_USERS,
    USER_TABLE_DDL,
)
from db.project_store import ProjectStore  # noqa: E402
from db.rbac_store import RbacStore  # noqa: E402
from utils.mysql_utils import mysql_handler  # noqa: E402

FIXTURES_DIR = ROOT / "tests" / "fixtures"
SAMPLE_CSV = FIXTURES_DIR / "table" / "mixed-types.csv"
DEMO_SID = "demo-session"


def _ensure_table(name: str, ddl: str) -> None:
    if not mysql_handler._check_table_exists(name):
        _, err = mysql_handler.execute(ddl)
        if err:
            raise RuntimeError(f"创建表 {name} 失败: {err}")


def ensure_core_schema() -> None:
    _ensure_table(TABLE_USERS, USER_TABLE_DDL)
    _ensure_table(TABLE_SESSION_USER, SESSION_USER_TABLE_DDL)
    _ensure_table(TABLE_SESSION_CONTENT, SESSION_CONTENT_TABLE_DDL)
    ok, err = RbacStore.ensure_schema()
    if not ok:
        raise RuntimeError(f"RBAC schema 初始化失败: {err}")
    ok, err = ProjectStore.ensure_schema()
    if not ok:
        raise RuntimeError(f"Project schema 初始化失败: {err}")


def seed_default_user() -> int:
    rows, err = mysql_handler.query(
        f"SELECT id FROM {TABLE_USERS} WHERE id = %s LIMIT 1",
        (DEFAULT_USER_ID,),
    )
    if err:
        raise RuntimeError(f"查询默认用户失败: {err}")
    if rows:
        return DEFAULT_USER_ID

    password_hash = hashlib.sha256(b"default-user").hexdigest()
    _, _, err = mysql_handler.insert(
        TABLE_USERS,
        {
            "username": "default",
            "phone": "",
            "password_hash": password_hash,
        },
    )
    if err:
        raise RuntimeError(f"创建默认用户失败: {err}")

    rows, err = mysql_handler.query(
        f"SELECT id FROM {TABLE_USERS} ORDER BY id LIMIT 1",
    )
    if err or not rows:
        raise RuntimeError("创建默认用户后未能查询到用户记录")
    return int(rows[0]["id"])


def bootstrap_projects(user_id: int) -> None:
    _, err = ProjectService.bootstrap_user_projects(user_id)
    if err:
        print(f"user {user_id} bootstrap warning: {err}", file=sys.stderr)


def seed_demo_session(user_id: int) -> Path:
    ws = ROOT / "tmp" / "workspaces" / str(user_id) / "sessions" / DEMO_SID
    ws.mkdir(parents=True, exist_ok=True)
    if SAMPLE_CSV.is_file():
        shutil.copyfile(SAMPLE_CSV, ws / "mixed-types.csv")

    mysql_handler.execute(
        f"DELETE FROM {TABLE_SESSION_USER} WHERE session_id = %s", (DEMO_SID,)
    )
    _, _, err = mysql_handler.insert(
        TABLE_SESSION_USER,
        {
            "session_id": DEMO_SID,
            "user_id": user_id,
            "title": "演示会话",
            "workspace_abs_path": str(ws.resolve()),
        },
    )
    if err:
        raise RuntimeError(f"创建演示会话失败: {err}")
    return ws


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="额外写入演示会话与示例数据文件",
    )
    args = parser.parse_args()

    ensure_core_schema()
    print("Core schema ready")

    user_id = seed_default_user()
    print(f"Default user id: {user_id}")

    bootstrap_projects(user_id)
    print("Projects bootstrapped")

    if args.demo:
        seed_demo_session(user_id)
        print(f"Demo session: {DEMO_SID}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
