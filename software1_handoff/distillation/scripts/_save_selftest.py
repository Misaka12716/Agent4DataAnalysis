"""把 run_all_selftests 的结果保存为 JSON 文件作为证据。"""
from __future__ import annotations
import json, datetime, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from distillation.software1_solver.selftest import run_all_selftests

out_dir = Path("benchmark/Software1_Bench/real_medical_data/_selftest")
out_dir.mkdir(parents=True, exist_ok=True)
ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
res = run_all_selftests()
report = {
    "timestamp": ts,
    "n_modules": len(res),
    "n_pass": sum(1 for r in res if r["ok"] is True),
    "n_skip": sum(1 for r in res if r["ok"] is None),
    "n_fail": sum(1 for r in res if r["ok"] is False),
    "results": res,
}
p = out_dir / f"selftest_{ts}.json"
p.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
              encoding="utf-8")
print(f"OK: {p}")
print(f"pass={report['n_pass']} skip={report['n_skip']} fail={report['n_fail']}")
