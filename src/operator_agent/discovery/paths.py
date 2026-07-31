# -*- coding: utf-8 -*-
"""Run-artifact directory convention for the discovery framework.

Every discovery run owns a directory tree under ``runs/discovery/<run_id>/``::

    runs/discovery/<run_id>/
        public_bb.json          # serialised PublicBlackboard
        lanes/<hid>.json        # one PrivateBlackboard per hypothesis lane
        artifacts/              # large products (CSV / plots / stdout)
        findings.yaml           # final compiled findings (§F)

This module is the *single source of truth* for those paths so that every
downstream stage (blackboard persistence, verify-stage artifact registration,
compile-stage findings emission) agrees on the layout.

Nothing here touches the filesystem unless you call :func:`ensure_run_dir`
(or one of the ``*_dir`` helpers with ``create=True``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

# Default root for all discovery runs, relative to the repo root.  The repo
# root is inferred as three parents up from this file:
#   .../AgentPlatform/src/operator_agent/discovery/paths.py
#   parents[0]=discovery [1]=operator_agent [2]=src [3]=AgentPlatform [4]=repo
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RUNS_ROOT = _REPO_ROOT / "runs" / "discovery"


PathLike = Union[str, "os.PathLike[str]"]


class RunPaths:
    """Resolved set of paths for a single discovery run.

    Construct via :func:`run_paths`.  All attributes are absolute
    :class:`pathlib.Path` objects.  Construction does not create anything;
    call :meth:`ensure` to materialise the directory tree.
    """

    def __init__(self, run_id: str, runs_root: Path) -> None:
        self.run_id = run_id
        self.runs_root = runs_root
        self.run_dir = runs_root / run_id
        self.public_bb = self.run_dir / "public_bb.json"
        self.lanes_dir = self.run_dir / "lanes"
        self.artifacts_dir = self.run_dir / "artifacts"
        self.findings = self.run_dir / "findings.yaml"

    def lane_path(self, hypothesis_id: str) -> Path:
        """Path to the private-blackboard JSON for one hypothesis lane."""
        return self.lanes_dir / f"{hypothesis_id}.json"

    def ensure(self) -> "RunPaths":
        """Create run_dir, lanes/ and artifacts/ if missing.  Idempotent."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.lanes_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RunPaths(run_id={self.run_id!r}, run_dir={self.run_dir!s})"


def run_paths(run_id: str,
              runs_root: Optional[PathLike] = None) -> RunPaths:
    """Return the :class:`RunPaths` for ``run_id`` (no filesystem writes).

    ``runs_root`` overrides :data:`DEFAULT_RUNS_ROOT` (handy for tests that
    want a tmp dir).  Use :meth:`RunPaths.ensure` to create the tree.
    """
    root = Path(runs_root) if runs_root is not None else DEFAULT_RUNS_ROOT
    return RunPaths(run_id, root)


def ensure_run_dir(run_id: str,
                   runs_root: Optional[PathLike] = None) -> RunPaths:
    """Convenience: build :class:`RunPaths` and materialise the tree."""
    return run_paths(run_id, runs_root).ensure()
