"""Deterministic selftest framework for Software 1 solvers.

A solver module that wants to be self-checked exposes a module-level
function::

    def selftest() -> dict:
        '''
        Returns one of:
          {"ok": True,  "summary": "...", "details": {...}}
          {"ok": False, "summary": "...", "details": {...}}
        '''

The framework runs the solver on a *deterministic, hand-built fixture*
and verifies the output against either:

  - an analytic / closed-form expected value, or
  - an independent reference implementation (a different library, or
    pure-numpy) computing the same quantity.

This is the way we prove an operator's *correctness* even when there is
no benchmark task / GT csv on disk for that capability.

The runner :func:`run_all_selftests` discovers every solver module in
``distillation.software1_solver.solvers`` and runs whatever
``selftest`` it exposes, returning a list of results suitable for the
v3 report.

中文说明
========
"出厂自检"框架。每个 solver 自带一个 ``selftest()`` 函数，用
**手搓的极小 fixture** 跑一遍，验证输出是否等于：
  1. 解析解 / 闭式期望值（例如 fillna 后中位数等于 3.0），或
  2. 独立的参考实现（例如直接调 scipy.stats 再比对）。

这一层的意义：即使没有 GT csv 也能证明算子"算得对"。
``run_all_selftests`` 会扫描整个 ``solvers/`` 包，挨个 import 模块
并调用 ``selftest()``，最后汇总成可以塞进 v3 报告的 list[dict]。

每条结果固定字段：
- ``module``  : 模块名
- ``ok``      : True / False / None（None=没定义 selftest）
- ``summary`` : 一句话结论
- ``details`` : {"diffs": [...], "tested": [...]}
- ``kind``    : selftest / skip / import_error / selftest_error
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Callable, Dict, List

from . import solvers as _solvers_pkg


def run_all_selftests() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for mod_info in pkgutil.iter_modules(_solvers_pkg.__path__):
        mod_name = f"{_solvers_pkg.__name__}.{mod_info.name}"
        try:
            mod = importlib.import_module(mod_name)
        except BaseException as e:
            results.append({
                "module":   mod_info.name,
                "ok":       False,
                "summary":  f"import failed: {type(e).__name__}: {e}",
                "details":  {},
                "kind":     "import_error",
            })
            continue
        fn: Callable | None = getattr(mod, "selftest", None)
        if fn is None:
            results.append({
                "module":   mod_info.name,
                "ok":       None,
                "summary":  "no selftest() defined",
                "details":  {},
                "kind":     "skip",
            })
            continue
        try:
            res = fn()
            res.setdefault("module", mod_info.name)
            res.setdefault("kind", "selftest")
            results.append(res)
        except BaseException as e:
            results.append({
                "module":   mod_info.name,
                "ok":       False,
                "summary":  f"selftest raised: {type(e).__name__}: {e}",
                "details":  {},
                "kind":     "selftest_error",
            })
    return results
