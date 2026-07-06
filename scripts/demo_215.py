#!/usr/bin/env python3
"""2.1.5 一键验收演示 — 登录 → 列模板 → 跑分析 → 打印摘要。"""
from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:52716"
DEMO = "acceptance-demo-session"


def main() -> int:
    print("=" * 50)
    print("  2.1.5 模板融合 — 一键演示")
    print("=" * 50)

    r = httpx.post(f"{BASE}/auth/login-with-sms", json={"phone": "13800000000", "code": "888888"}, timeout=15)
    if r.status_code != 200:
        print("登录失败:", r.text[:200])
        return 1
    token = r.json()["data"]["access_token"]
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    tl = httpx.get(f"{BASE}/template/list", headers=h, timeout=30)
    templates = tl.json().get("data") or []
    print(f"\n[1] 模板列表: {len(templates)} 个")
    for t in templates[:5]:
        print(f"    - id={t.get('id')} {t.get('template_name')} ({t.get('disease_type')})")

    if not templates:
        print("\n[2] 无可用模板，请先运行 bash scripts/init-platform.sh")
        return 1
    template_id = templates[0].get("id")

    run = httpx.post(
        f"{BASE}/analysis/template-run",
        json={"session_id": DEMO, "template_id": template_id},
        headers=h,
        timeout=60,
    )
    if run.status_code != 200:
        print("\n[2] 分析失败:", run.text[:300])
        return 1

    data = run.json().get("data") or {}
    print(f"\n[2] 模板分析完成（按 analysis_steps 医学算子执行）")
    print(f"    模板: {data.get('template_name')} ({data.get('disease_type')})")
    print(f"    数据: {data.get('data_file')} — {data.get('row_count')} 行 × {data.get('column_count')} 列")
    steps = data.get("step_results") or []
    ok = sum(1 for s in steps if s.get("status") == "ok")
    print(f"    步骤: {ok}/{len(steps)} 成功")
    for s in steps[:6]:
        print(f"      · {s.get('step')}. {s.get('name')}: {s.get('method')} [{s.get('status')}]")
    if len(steps) > 6:
        print(f"      · ... 共 {len(steps)} 步，详见 step_results")
    print(f"\n[3] 报告摘要（前 400 字）:")
    print((data.get("report_markdown") or "")[:400])
    print("\n" + "=" * 50)
    print("  DEMO OK — 可录屏: 前端「模板分析」→ 验收登录 → 开始分析")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
