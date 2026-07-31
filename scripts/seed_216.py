#!/usr/bin/env python3
"""2.1.6 临床支持种子数据：患者 / 参考区间 / 随访 / 演示风险模型。

用法:
  python scripts/seed_216.py
"""
from __future__ import annotations

import json
import pickle
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import accuracy_score, f1_score  # noqa: E402

from db.followup_schema import FOLLOWUP_TABLE_DDL, TABLE_FOLLOWUPS  # noqa: E402
from db.patient_schema import PATIENT_TABLE_DDL, TABLE_PATIENTS  # noqa: E402
from db.reference_schema import REFERENCE_RANGE_TABLE_DDL, TABLE_REFERENCE_RANGES  # noqa: E402
from db.risk_schema import RISK_MODEL_TABLE_DDL, TABLE_RISK_MODELS  # noqa: E402
from utils.mysql_utils import mysql_handler  # noqa: E402

PATIENTS_JSON = ROOT / "scripts" / "patients.json"
REFERENCES_JSON = ROOT / "scripts" / "references.json"
MODEL_DIR = ROOT / "workspace" / "models"
FEATURES = ["HAMD_total", "HAMA_total", "PHQ9_total", "age"]


def _ensure_table(name: str, ddl: str) -> None:
    if not mysql_handler._check_table_exists(name):
        _, err = mysql_handler.execute(ddl)
        if err:
            raise RuntimeError(f"创建表 {name} 失败: {err}")


def _norm_gender(g: str) -> str:
    g = (g or "").strip().upper()
    if g in ("F", "FEMALE"):
        return "female"
    if g in ("M", "MALE"):
        return "male"
    return (g or "").lower()


def seed_patients() -> int:
    _ensure_table(TABLE_PATIENTS, PATIENT_TABLE_DDL)
    mysql_handler.execute(f"DELETE FROM {TABLE_PATIENTS}")

    rows = json.loads(PATIENTS_JSON.read_text(encoding="utf-8"))
    count = 0
    for r in rows:
        pid, age, gender, diagnosis, hamd, hama, phq9, med, outcome, relapse, adm, dis = r
        duration = max(1.0, float((int(age) % 10) + 1))
        _, _, err = mysql_handler.insert(
            TABLE_PATIENTS,
            {
                "patient_id": pid,
                "age": age,
                "gender": _norm_gender(gender),
                "diagnosis": diagnosis,
                "HAMD_total": hamd,
                "HAMA_total": hama,
                "PHQ9_total": phq9,
                "disease_duration_years": duration,
                "medication": med,
                "outcome": outcome,
                "relapse": relapse,
                "admission_date": adm,
                "discharge_date": dis,
            },
        )
        if err:
            print(f"  ! 患者 {pid} 导入失败: {err}")
            continue
        count += 1
    return count


def seed_references() -> int:
    _ensure_table(TABLE_REFERENCE_RANGES, REFERENCE_RANGE_TABLE_DDL)
    # 兼容旧表：source 列可能过短
    try:
        mysql_handler.execute(
            f"ALTER TABLE {TABLE_REFERENCE_RANGES} MODIFY COLUMN source TEXT DEFAULT NULL"
        )
    except Exception:
        pass
    mysql_handler.execute(f"DELETE FROM {TABLE_REFERENCE_RANGES}")

    rows = json.loads(REFERENCES_JSON.read_text(encoding="utf-8"))
    count = 0
    for r in rows:
        ind, gender, alo, ahi, diag, lo, hi, unit, src = r
        _, _, err = mysql_handler.insert(
            TABLE_REFERENCE_RANGES,
            {
                "indicator": ind,
                "gender": _norm_gender(gender) if gender else None,
                "age_range_lower": alo,
                "age_range_upper": ahi,
                "diagnosis": diag,
                "lower_bound": lo,
                "upper_bound": hi,
                "unit": unit,
                "source": src,
            },
        )
        if err:
            print(f"  ! 参考区间 {ind} 导入失败: {err}")
            continue
        count += 1
    return count


def seed_followups() -> int:
    _ensure_table(TABLE_FOLLOWUPS, FOLLOWUP_TABLE_DDL)
    mysql_handler.execute(f"DELETE FROM {TABLE_FOLLOWUPS}")

    patients, err = mysql_handler.query(f"SELECT * FROM {TABLE_PATIENTS}")
    if err:
        raise RuntimeError(f"读取患者失败: {err}")

    random.seed(42)
    count = 0
    for p in patients or []:
        base = datetime.strptime(str(p["admission_date"]), "%Y-%m-%d")
        for i, vt in enumerate(["baseline", "week4", "week8", "week12"]):
            d = (base + timedelta(days=28 * i)).strftime("%Y-%m-%d")
            factor = 1 - 0.12 * i
            _, _, ins_err = mysql_handler.insert(
                TABLE_FOLLOWUPS,
                {
                    "patient_id": p["patient_id"],
                    "visit_date": d,
                    "visit_type": vt,
                    "HAMD_total": round(float(p["HAMD_total"] or 0) * factor, 1),
                    "HAMA_total": round(float(p["HAMA_total"] or 0) * factor, 1),
                    "PHQ9_total": round(float(p["PHQ9_total"] or 0) * factor, 1),
                    "medication": p.get("medication"),
                    "notes": vt,
                },
            )
            if ins_err and "duplicate" not in str(ins_err).lower():
                print(f"  ! 随访 {p['patient_id']} {d} 失败: {ins_err}")
                continue
            count += 1
    return count


def seed_risk_model() -> int:
    _ensure_table(TABLE_RISK_MODELS, RISK_MODEL_TABLE_DDL)
    mysql_handler.execute(f"DELETE FROM {TABLE_RISK_MODELS}")

    rows, err = mysql_handler.query(
        f"SELECT age, HAMD_total, HAMA_total, PHQ9_total, relapse FROM {TABLE_PATIENTS}"
    )
    if err:
        raise RuntimeError(f"读取训练数据失败: {err}")
    if len(rows or []) < 20:
        print("  ! 患者不足 20 条，跳过风险模型训练")
        return 0

    x = np.array(
        [
            [
                float(r["HAMD_total"] or 0),
                float(r["HAMA_total"] or 0),
                float(r["PHQ9_total"] or 0),
                float(r["age"] or 0),
            ]
            for r in rows
        ]
    )
    y = np.array([int(r["relapse"] or 0) for r in rows])

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / "relapse_demo_v1.pkl"
    clf = LogisticRegression(max_iter=200, random_state=42)
    clf.fit(x, y)
    with open(model_path, "wb") as f:
        pickle.dump(clf, f)

    pred = clf.predict(x)
    metrics_json = json.dumps(
        {
            "accuracy": round(float(accuracy_score(y, pred)), 4),
            "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
        }
    )
    _, _, ins_err = mysql_handler.insert(
        TABLE_RISK_MODELS,
        {
            "model_name": "relapse_demo_v1",
            "task_type": "relapse",
            "model_type": "LogisticRegression",
            "features": json.dumps(FEATURES, ensure_ascii=False),
            "metrics": metrics_json,
            "params": json.dumps({"max_iter": 200}),
            "file_path": str(model_path.resolve()),
        },
    )
    if ins_err:
        raise RuntimeError(f"写入风险模型失败: {ins_err}")
    print(f"  Trained demo risk model: {model_path}")
    return 1


def main() -> None:
    n_pat = seed_patients()
    print(f"Patients: {n_pat}")
    n_ref = seed_references()
    print(f"References: {n_ref}")
    n_fu = seed_followups()
    print(f"Followups: {n_fu}")
    n_model = seed_risk_model()
    print(f"Risk models: {n_model}")
    print("Clinical seed complete — start backend and use clinical APIs / UI to verify")


if __name__ == "__main__":
    main()
