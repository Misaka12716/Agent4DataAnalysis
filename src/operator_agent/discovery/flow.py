# -*- coding: utf-8 -*-
"""DiscoveryFlow — the (non-API) orchestration entry for the discovery
framework (V8 §5 ``flow.py``).

This is deliberately **not** exposed as a production API.  It connects the
entry router to the supervisor:

- ``route(task) == "general"`` → return a *delegation marker* only
  (``DiscoveryResult(route="general", summary="delegate_to_legacy")``).  This
  branch does **not** touch / import / mutate the legacy LangGraph flow or
  ``solve_task``; production wiring is a later, one-line branch at the SSE
  entry that, on seeing this marker, keeps doing exactly what it did before.
- ``route(task) == "discovery"`` → load the dataframe (from ``df`` or by
  reading ``csv``) and hand off to :meth:`Supervisor.run`.

Both ``router_fn`` and ``supervisor`` are injectable so the flow is unit- and
integration-testable without a live LLM.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional, Union

import pandas as pd

from .router import route as _default_route
from .supervisor import Supervisor
from .types import DiscoveryResult

__all__ = ["DiscoveryFlow"]

PathLike = Union[str, "os.PathLike[str]"]


class DiscoveryFlow:
    """Route a task and, when it is research-discovery intent, run the
    supervisor; otherwise emit a delegate-to-legacy marker.

    Parameters
    ----------
    router_fn
        Callable ``(task) -> RouteDecision`` (defaults to
        :func:`operator_agent.discovery.router.route`).
    supervisor
        A :class:`Supervisor` instance (defaults to a fresh one).
    """

    def __init__(self, router_fn: Optional[Callable] = None,
                 supervisor: Optional[Supervisor] = None) -> None:
        self._route = router_fn or _default_route
        self._supervisor = supervisor or Supervisor()

    def run(
        self,
        task: str,
        *,
        df: Optional[pd.DataFrame] = None,
        csv: Optional[PathLike] = None,
        run_id: Optional[str] = None,
        **kwargs: Any,
    ) -> DiscoveryResult:
        """Route ``task`` and dispatch.

        ``kwargs`` are forwarded to :meth:`Supervisor.run` (e.g. ``cohort_id``,
        ``seed``, ``max_hypotheses``, ``refine_max_iter``, ``hypothesis_impl``,
        ``clarify``).
        """
        decision = self._route(task)

        # --- general → delegate-to-legacy marker (no legacy code touched) ---
        if decision.route == "general":
            return DiscoveryResult(
                run_id=run_id or "",
                route="general",
                status="ok",
                findings=[],
                findings_path=None,
                summary="delegate_to_legacy",
                error=None,
            )

        # --- discovery → load df then run the supervisor --------------------
        if df is None:
            if csv is None:
                return DiscoveryResult(
                    run_id=run_id or "",
                    route="discovery",
                    status="error",
                    findings=[],
                    findings_path=None,
                    summary=None,
                    error="discovery route requires either df or csv",
                )
            df = pd.read_csv(csv)

        return self._supervisor.run(task, df, run_id=run_id, **kwargs)
