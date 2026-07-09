#!/usr/bin/env python3
"""将 legacy 会话工作区迁移到 project/sessions/ 布局，并归属 orphan 会话。

用法:
  python scripts/migrate-legacy-sessions.py --dry-run
  python scripts/migrate-legacy-sessions.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from configs.config import TEMP_FOLDER  # noqa: E402
from db.project_store import ProjectStore  # noqa: E402
from db.session_store import SessionStore  # noqa: E402
from backend.project_service import ProjectService  # noqa: E402
from utils.workspace_manager import (  # noqa: E402
    WORKSPACES_ROOT,
    init_session_in_project,
    project_root_for,
    workspace_path_for,
)


def _is_legacy_session_path(user_id: int, session_id: str, workspace_abs: str) -> bool:
    legacy = os.path.abspath(workspace_path_for(user_id, session_id))
    current = os.path.abspath(workspace_abs or "")
    return current == legacy or (
        current.startswith(os.path.abspath(WORKSPACES_ROOT) + os.sep)
        and "/sessions/" not in current.replace("\\", "/")
    )


def migrate_user(user_id: int, dry_run: bool) -> dict:
    stats = {"orphans_assigned": 0, "sessions_migrated": 0, "errors": []}

    default_row, err = ProjectService.bootstrap_user_projects(user_id)
    if err:
        stats["errors"].append(f"user {user_id}: bootstrap failed: {err}")
        return stats

    project_id = int((default_row or {}).get("id") or 0)
    if err:
        stats["errors"].append(f"user {user_id}: list sessions failed: {err}")
        return stats

    for sid in session_ids:
        row, err = SessionStore.get_session_user(sid)
        if err or not row:
            continue
        uid = int(row.get("user_id") or 0)
        pid = row.get("project_id")
        workspace_abs = str(row.get("workspace_abs_path") or "")
        if not pid or int(pid) <= 0:
            continue
        pid = int(pid)
        if not _is_legacy_session_path(uid, sid, workspace_abs):
            continue
        if not os.path.isdir(workspace_abs):
            continue

        dest = init_session_in_project(uid, pid, sid) if not dry_run else project_root_for(uid, pid)
        dest = os.path.join(dest, "sessions", sid) if dry_run else dest
        if dry_run:
            print(f"[dry-run] would migrate {sid}: {workspace_abs} -> {dest}")
            stats["sessions_migrated"] += 1
            continue

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            stats["errors"].append(f"session {sid}: destination exists: {dest}")
            continue
        try:
            shutil.move(workspace_abs, dest)
            SessionStore.set_workspace_path(sid, uid, dest)
            stats["sessions_migrated"] += 1
            print(f"migrated {sid} -> {dest}")
        except OSError as e:
            stats["errors"].append(f"session {sid}: move failed: {e}")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy session workspaces")
    parser.add_argument("--dry-run", action="store_true", help="仅打印计划，不写磁盘/DB")
    parser.add_argument("--user-id", type=int, default=0, help="仅迁移指定用户")
    args = parser.parse_args()

    ok, err = ProjectStore.ensure_schema()
    if not ok:
        print(f"schema ensure failed: {err}", file=sys.stderr)
        return 1

    print(f"workspaces root: {WORKSPACES_ROOT} (TEMP_FOLDER={TEMP_FOLDER})")
    if args.dry_run:
        print("mode: dry-run")

    if args.user_id > 0:
        user_ids = [args.user_id]
    else:
        root = Path(WORKSPACES_ROOT)
        if not root.is_dir():
            print("no workspaces directory; nothing to migrate")
            return 0
        user_ids = sorted(int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdigit())

    total = {"orphans_assigned": 0, "sessions_migrated": 0, "errors": []}
    for uid in user_ids:
        result = migrate_user(uid, dry_run=args.dry_run)
        total["orphans_assigned"] += result["orphans_assigned"]
        total["sessions_migrated"] += result["sessions_migrated"]
        total["errors"].extend(result["errors"])

    print(
        f"done: orphans_assigned={total['orphans_assigned']} "
        f"sessions_migrated={total['sessions_migrated']} errors={len(total['errors'])}"
    )
    for msg in total["errors"]:
        print(f"  error: {msg}", file=sys.stderr)
    return 1 if total["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
