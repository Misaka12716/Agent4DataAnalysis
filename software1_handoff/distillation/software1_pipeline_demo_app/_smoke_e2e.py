"""End-to-end smoke test: drive the Flask demo with realistic CSVs.

Runs three scenarios that cover the failure modes the user reported:

  A. chi_square_independence on the all-numeric lab_panel.csv (was
     failing with KeyError: 'row_col').
  B. reference_range_flag on lab_panel.csv (needs a PARAMS dict).
  C. lab_eda_4step preset on lab_panel.csv (regression check).

For each scenario we POST to /run with use_llm=1 and assert the run
completes without exceptions.  We then read manifest.json and print a
status table.
"""
from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from distillation.software1_pipeline_demo_app.app import create_app, RUN_ROOT


def _post(client, csv_bytes: bytes, spec: dict, use_llm: bool):
    data = {
        "spec": json.dumps(spec),
        "use_llm": "1" if use_llm else "",
        "file": (BytesIO(csv_bytes), "input.csv"),
    }
    r = client.post("/run", data=data, content_type="multipart/form-data",
                    follow_redirects=False)
    assert r.status_code == 302, f"unexpected {r.status_code}: {r.data[:300]}"
    run_id = r.location.rsplit("/", 1)[-1]
    return run_id


def _manifest(run_id: str) -> dict:
    return json.loads(
        (RUN_ROOT / run_id / "manifest.json").read_text(encoding="utf-8"))


def main():
    lab_panel = (ROOT / "benchmark" / "Software1_Bench"
                 / "F13_outlier_reference_range_detection"
                 / "selfcon_reference_range_audit"
                 / "inputs" / "lab_panel.csv").read_bytes()
    panss = (ROOT / "benchmark" / "Software1_Bench"
             / "F14_scale_structuring_extraction"
             / "selfcon_panss_item_to_total"
             / "inputs" / "panss_items.csv").read_bytes()

    app = create_app()
    client = app.test_client()

    scenarios = [
        ("A_chi_square_only_with_LLM", True, lab_panel, {
            "steps": [
                {"solver": "chi_square_independence", "from": "previous"},
            ],
        }),
        ("A_chi_square_only_NO_LLM", False, lab_panel, {
            "steps": [
                {"solver": "chi_square_independence", "from": "previous"},
            ],
        }),
        ("B_reference_range_with_LLM", True, lab_panel, {
            "steps": [
                {"solver": "reference_range_flag", "from": "previous"},
            ],
        }),
        ("C_lab_eda_4step", True, lab_panel, {
            "steps": [
                {"solver": "missing_summary", "from": "previous"},
                {"solver": "fillna_median",   "from": "initial"},
                {"solver": "outlier_iqr_flag",
                 "from": "step", "step_index": 1, "csv_key": "filled_csv"},
                {"solver": "pearson_correlation",
                 "from": "step", "step_index": 1, "csv_key": "filled_csv"},
            ],
        }),
        ("D_normality_then_correction", True, lab_panel, {
            "steps": [
                {"solver": "normality_test", "from": "previous"},
                {"solver": "multiple_correction",
                 "from": "previous"},
            ],
        }),
        ("E_panss_factor_score_LLM", True, panss, {
            "steps": [
                {"solver": "panss_factor_score", "from": "previous"},
            ],
        }),
    ]

    print(f"{'scenario':<40} ok  steps  details")
    print("-" * 100)
    for name, use_llm, csv, spec in scenarios:
        try:
            run_id = _post(client, csv, spec, use_llm)
            man = _manifest(run_id)
        except Exception as e:
            print(f"{name:<40} ERR     -    {type(e).__name__}: {e}")
            continue
        ok = "✓" if man.get("ok") else "✗"
        n_steps = len(man.get("steps") or [])
        n_files = len(man.get("files") or [])
        details = []
        for s in (man.get("steps") or []):
            tag = "ok" if s["status"] == "ok" else "ERR"
            extra = f" src={s['mapping_source']}"
            if s["status"] != "ok":
                extra += f" err={s.get('error', '')[:60]}"
            details.append(f"{s['name']}[{tag}{extra}]")
        print(f"{name:<40}  {ok}    {n_steps}    files={n_files} | "
              + "; ".join(details))


if __name__ == "__main__":
    main()
