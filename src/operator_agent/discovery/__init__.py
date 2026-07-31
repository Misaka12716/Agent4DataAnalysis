# -*- coding: utf-8 -*-
"""Discovery framework — blackboard + supervisor multi-agent scientific
discovery (V8 architecture completion).

Pure-additive package: nothing here modifies the legacy LangGraph analysis
flow or ``operator_agent.agent.solve_task``.  An entry router (:mod:`router`)
splits "can we publish a paper / find a significant result" research intents
into this new framework; everything else is delegated back to the legacy
flow untouched.

This ``__init__`` only re-exports the *foundation* layer (the interfaces that
lock the contracts for every later stage):

- :mod:`paths`       — run-artifact directory convention.
- :mod:`types`       — stage-output dataclasses (JSON-safe).
- :mod:`blackboard`  — public + private blackboards (in-process + JSON).
- :mod:`signals`     — synchronous control-plane skeleton.
- :mod:`router`      — entry intent router.
- :mod:`litcheck_stub` — N6 literature-context placeholder (returns None).

Later phases add the stage agents (data_processing / hypothesis / verify /
refine / review), the supervisor, the compile step and the flow/CLI.
"""
from __future__ import annotations

from .paths import (
    DEFAULT_RUNS_ROOT,
    RunPaths,
    ensure_run_dir,
    run_paths,
)
from .errors import (
    DataInputError,
    DataLoadError,
    RunStatus,
    SystemError as DiscoverySystemError,
    UnanalyzableDataError,
    UserActionError,
    UserRejectedCleaningError,
    UserTimeoutError,
)
from .types import (
    CleanSuggestion,
    DiscoveryResult,
    Evidence,
    FindingRecord,
    ProfileSummary,
    RefineDecision,
    RequirementSummary,
    ReviewResult,
    VerifyResult,
)
from .blackboard import (
    BlackboardError,
    PrivateBlackboard,
    ProvenanceConflictError,
    PublicBlackboard,
)
from .signals import (
    CancellationToken,
    Signal,
    SignalBus,
    SignalType,
)
from .router import RouteDecision, route
from .litcheck_stub import LitContext
from . import litcheck_stub
from .data_io import DataLoadInfo, load_dataset
from .top_agent import (
    Clarification,
    Session,
    TopAgent,
    UserCancelledError,
)

__all__ = [
    # paths
    "DEFAULT_RUNS_ROOT",
    "RunPaths",
    "run_paths",
    "ensure_run_dir",
    # errors (ADR-0007)
    "RunStatus",
    "UserActionError",
    "UserRejectedCleaningError",
    "UserTimeoutError",
    "DataInputError",
    "DataLoadError",
    "UnanalyzableDataError",
    "DiscoverySystemError",
    # types
    "RequirementSummary",
    "ProfileSummary",
    "CleanSuggestion",
    "Evidence",
    "VerifyResult",
    "RefineDecision",
    "ReviewResult",
    "FindingRecord",
    "DiscoveryResult",
    # blackboard
    "PublicBlackboard",
    "PrivateBlackboard",
    "BlackboardError",
    "ProvenanceConflictError",
    # signals
    "SignalType",
    "Signal",
    "SignalBus",
    "CancellationToken",
    # router
    "route",
    "RouteDecision",
    # litcheck
    "litcheck_stub",
    "LitContext",
    # data I/O (ADR-0006)
    "load_dataset",
    "DataLoadInfo",
    # top-level coordinator (cancel / progress / clarify wrapper around
    # DiscoveryFlow — purely additive, never touched stage internals)
    "TopAgent",
    "Session",
    "Clarification",
    "UserCancelledError",
]
