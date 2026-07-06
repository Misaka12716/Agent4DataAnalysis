#!/usr/bin/env python3
"""平台种子数据：模板表 + 可选验收用户/演示会话 + 演示 fixtures。

用法:
  python scripts/seed_templates.py
  python scripts/seed_templates.py --acceptance
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.mysql_utils import mysql_handler  # noqa: E402
from db.template_schema import TABLE_TEMPLATES, TEMPLATE_TABLE_DDL  # noqa: E402
from db.models import (  # noqa: E402
    TABLE_USERS,
    TABLE_SESSION_USER,
    USER_TABLE_DDL,
    SESSION_USER_TABLE_DDL,
)

FIXTURES_DIR = ROOT / "tests" / "fixtures"
CROSS_SECTION_XLSX = FIXTURES_DIR / "mental_health_sample.xlsx"
LONGITUDINAL_XLSX = FIXTURES_DIR / "mental_health_longitudinal_sample.xlsx"
DEMO_SID = "acceptance-demo-session"
ACCEPTANCE_PHONE = "13800000000"


def _ensure_table(name: str, ddl: str) -> None:
    if not mysql_handler._check_table_exists(name):
        _, err = mysql_handler.execute(ddl)
        if err:
            raise RuntimeError(f"创建表 {name} 失败: {err}")


def ensure_fixtures() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    gen_script = ROOT / "scripts" / "gen_longitudinal_sample.py"
    if gen_script.is_file() and not LONGITUDINAL_XLSX.is_file():
        subprocess.run(
            [sys.executable, str(gen_script), "--out", str(LONGITUDINAL_XLSX)],
            check=True,
            cwd=str(ROOT),
        )
        print(f"Generated {LONGITUDINAL_XLSX}")

    if LONGITUDINAL_XLSX.is_file() and not CROSS_SECTION_XLSX.is_file():
        import pandas as pd

        df = pd.read_excel(LONGITUDINAL_XLSX)
        baseline = df[df["visit_type"] == "baseline"].copy()
        if baseline.empty:
            baseline = df.drop_duplicates("patient_id", keep="first")
        baseline.to_excel(CROSS_SECTION_XLSX, index=False)
        print(f"Generated {CROSS_SECTION_XLSX} ({len(baseline)} rows from baseline)")


def seed_templates() -> int:
    _ensure_table(TABLE_TEMPLATES, TEMPLATE_TABLE_DDL)
    mysql_handler.execute(f"DELETE FROM {TABLE_TEMPLATES}")

    tpl_dir = ROOT / "knowledge" / "templates"
    count = 0
    if tpl_dir.is_dir():
        for fp in sorted(tpl_dir.glob("*.json")):
            data = json.loads(fp.read_text(encoding="utf-8"))
            _, _, err = mysql_handler.insert(
                TABLE_TEMPLATES,
                {
                    "template_name": data["template_name"],
                    "disease_type": data["disease_type"],
                    "scales": json.dumps(data.get("scales", []), ensure_ascii=False),
                    "analysis_steps": json.dumps(data.get("analysis_steps", []), ensure_ascii=False),
                    "report_structure": json.dumps(data.get("report_structure", []), ensure_ascii=False),
                    "version": data.get("version", "1.0.0"),
                },
            )
            if err:
                print(f"  ! 导入 {fp.name} 失败: {err}")
                continue
            count += 1
    return count


def seed_acceptance_user() -> int:
    _ensure_table(TABLE_USERS, USER_TABLE_DDL)
    rows, err = mysql_handler.query(
        f"SELECT id FROM {TABLE_USERS} WHERE phone = %s LIMIT 1", (ACCEPTANCE_PHONE,)
    )
    if err:
        raise RuntimeError(f"查询验收用户失败: {err}")
    if rows:
        return int(rows[0]["id"])

    username = f"acceptance_{int(time.time())}"
    password_hash = hashlib.sha256(f"sms-login:{ACCEPTANCE_PHONE}".encode("utf-8")).hexdigest()
    _, last_id, err = mysql_handler.insert(
        TABLE_USERS,
        {"username": username, "phone": ACCEPTANCE_PHONE, "password_hash": password_hash},
    )
    if err:
        raise RuntimeError(f"创建验收用户失败: {err}")
    if last_id:
        return int(last_id)

    rows, err = mysql_handler.query(
        f"SELECT id FROM {TABLE_USERS} WHERE phone = %s LIMIT 1", (ACCEPTANCE_PHONE,)
    )
    return int(rows[0]["id"]) if rows else 0


def seed_demo_session(user_id: int) -> Path:
    _ensure_table(TABLE_SESSION_USER, SESSION_USER_TABLE_DDL)

    ws = ROOT / "workspace" / "sessions" / DEMO_SID
    ws.mkdir(parents=True, exist_ok=True)
    if CROSS_SECTION_XLSX.is_file():
        shutil.copy2(CROSS_SECTION_XLSX, ws / "mental_health_sample.xlsx")

    mysql_handler.execute(
        f"DELETE FROM {TABLE_SESSION_USER} WHERE session_id = %s", (DEMO_SID,)
    )
    _, _, err = mysql_handler.insert(
        TABLE_SESSION_USER,
        {
            "session_id": DEMO_SID,
            "user_id": user_id,
            "title": "验收演示会话",
            "workspace_abs_path": str(ws.resolve()),
        },
    )
    if err:
        raise RuntimeError(f"创建演示会话失败: {err}")
    return ws


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acceptance",
        action="store_true",
        help="额外写入验收用户与演示会话（需 ACCEPTANCE_MODE=1 登录）",
    )
    args = parser.parse_args()

    ensure_fixtures()
    n = seed_templates()
    print(f"Templates: {n}")

    if args.acceptance:
        user_id = seed_acceptance_user()
        print(f"Acceptance user id: {user_id} (phone {ACCEPTANCE_PHONE})")
        seed_demo_session(user_id)
        print(f"Demo session: {DEMO_SID}")
        print("Login: phone 13800000000 / code 888888 (set ACCEPTANCE_MODE=1 on backend)")


if __name__ == "__main__":
    main()
