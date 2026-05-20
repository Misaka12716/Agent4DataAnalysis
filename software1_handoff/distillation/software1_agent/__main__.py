"""CLI entrypoint for the Software 1 agent.

Usage::

    python -m distillation.software1_agent run \
        --task "找出 lab 表里两两相关性最强的前 5 对指标" \
        --csv  benchmark/Software1_Bench/.../inputs/lab_panel.csv \
        --out  distillation/software1_agent/_runs

Or in single-shot form (positional)::

    python -m distillation.software1_agent \
        "正态性检验+多重比较校正" data.csv runs/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from distillation.software1_agent.agent import solve_task


def _print_summary(res):
    print()
    print("=" * 78)
    print(f"run_id      : {res.run_dir.name}")
    print(f"ok          : {res.ok}")
    print(f"manifest    : {res.manifest_path}")
    if res.plan.rationale:
        print(f"rationale   : {res.plan.rationale}")
    print(f"steps       : {len(res.steps)}")
    for s in res.steps:
        tag = "✓" if s["status"] == "ok" else "✗"
        extra = f" src={s.get('mapping_source','-')}"
        if s["status"] != "ok":
            extra += f" err={(s.get('error') or '')[:80]}"
        print(f"  {tag} {s['name']} ({s['solver']}){extra}")
    if res.error:
        print(f"error       : {res.error}")
    print("=" * 78)


def main(argv=None):
    p = argparse.ArgumentParser(prog="software1_agent")
    sub = p.add_subparsers(dest="cmd")
    runp = sub.add_parser("run", help="Plan + execute a pipeline.")
    runp.add_argument("--task", required=True,
                      help="Natural-language analysis goal (zh / en).")
    runp.add_argument("--csv", required=True, help="Input CSV path.")
    runp.add_argument("--out", required=True,
                      help="Output root directory (each run gets a subdir).")
    runp.add_argument("--no-llm-mapping", action="store_true",
                      help="Disable per-step LLM column mapping (use rule-based).")
    runp.add_argument("--run-id", default=None,
                      help="Override the auto-generated run id.")
    runp.add_argument("--quiet", action="store_true",
                      help="Suppress the summary printout.")

    args = p.parse_args(argv)
    if args.cmd is None:
        # allow positional shorthand: <task> <csv> <out>
        argv2 = sys.argv[1:]
        if len(argv2) == 3:
            return main(["run", "--task", argv2[0],
                         "--csv", argv2[1], "--out", argv2[2]])
        p.print_help()
        return 2

    res = solve_task(
        task=args.task,
        csv_path=Path(args.csv),
        output_dir=Path(args.out),
        use_llm_mapping=not args.no_llm_mapping,
        run_id=args.run_id,
    )
    if not args.quiet:
        _print_summary(res)
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
