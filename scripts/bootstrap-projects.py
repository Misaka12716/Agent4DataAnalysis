#!/usr/bin/env python3
"""为所有用户确保个人默认项目并归属无 project_id 的历史会话。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.project_service import ProjectService  # noqa: E402
from db.project_store import ProjectStore  # noqa: E402
from utils.mysql_utils import mysql_handler  # noqa: E402


def main() -> int:
    ok, err = ProjectStore.ensure_schema()
    if not ok:
        print(f"schema ensure failed: {err}", file=sys.stderr)
        return 1

    rows, err = mysql_handler.query("SELECT id FROM users ORDER BY id")
    if err:
        print(f"query users failed: {err}", file=sys.stderr)
        return 1

    for row in rows or []:
        uid = int(row.get("id") or 0)
        if uid <= 0:
            continue
        _, err = ProjectService.bootstrap_user_projects(uid)
        if err:
            print(f"user {uid} bootstrap warning: {err}", file=sys.stderr)

    print("bootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
