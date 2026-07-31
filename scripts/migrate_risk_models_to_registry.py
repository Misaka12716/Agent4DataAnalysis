#!/usr/bin/env python3
"""将 clinical mental_health_risk_models 历史记录同步到个人模型库 user_models。

用法（在 src 为 PYTHONPATH 或从仓库根执行）:
  cd /data1/pjw/AgentPlatform
  PYTHONPATH=src python scripts/migrate_risk_models_to_registry.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def main() -> int:
    from utils.mysql_utils import mysql_handler
    from backend.model_registry_service import register_model
    from db.resource_schema import ensure_resource_tables
    import json

    ensure_resource_tables(mysql_handler)

    # 表名与 clinical 风险模型一致
    table = "mental_health_risk_models"
    rows, err = mysql_handler.query(f"SELECT * FROM {table} ORDER BY id ASC")
    if err:
        print(f"查询失败: {err}")
        return 1
    if not rows:
        print("无风险模型记录，跳过")
        return 0

    ok, fail = 0, 0
    for row in rows:
        uid = int(row.get("owner_user_id") or 0)
        mid = int(row.get("id"))
        path = row.get("file_path") or ""
        if uid <= 0 or not path:
            fail += 1
            continue

        def _loads(v):
            if v is None:
                return None
            if isinstance(v, (dict, list)):
                return v
            try:
                return json.loads(v)
            except Exception:
                return v

        _, rerr = register_model(
            user_id=uid,
            model_name=str(row.get("model_name") or f"risk_{mid}"),
            file_path=path,
            metadata={
                "framework": "sklearn",
                "model_type": row.get("model_type"),
                "task_type": row.get("task_type"),
                "features": _loads(row.get("features")),
                "metrics": _loads(row.get("metrics")),
                "params": _loads(row.get("params")),
            },
            source="clinical_risk",
            source_ref_id=mid,
        )
        if rerr:
            print(f"  fail id={mid}: {rerr}")
            fail += 1
        else:
            ok += 1
            print(f"  ok id={mid} user={uid}")

    print(f"完成: success={ok} fail={fail}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
