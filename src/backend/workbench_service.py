# backend/workbench_service.py — 2.2.10 工作台业务层

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from configs.config import TEMP_FOLDER
from orchestrator.workbench_orchestrator import WorkbenchOrchestrator, WorkbenchRunState

# In-memory run registry (production: persist to DB)
_RUNS: Dict[str, WorkbenchRunState] = {}
_RUN_THREADS: Dict[str, threading.Thread] = {}
_ORCH = WorkbenchOrchestrator()

TABLE_EXTENSIONS = {".csv", ".xlsx", ".xls", ".tsv"}
WORKBENCH_SESSION_PREFIX = "wb_"


def _runs_root() -> Path:
    return Path(TEMP_FOLDER) / "workbench_runs"


def _session_root() -> Path:
    """Dedicated workbench storage, intentionally outside AgentPlatform sessions."""
    return Path(TEMP_FOLDER) / "workbench_sessions"


def _session_dir(session_id: str) -> Optional[Path]:
    sid = (session_id or "").strip()
    if not sid.startswith(WORKBENCH_SESSION_PREFIX):
        return None
    suffix = sid[len(WORKBENCH_SESSION_PREFIX):]
    if len(suffix) != 32 or any(ch not in "0123456789abcdef" for ch in suffix):
        return None
    return _session_root() / sid


def create_workbench_session() -> Dict[str, Any]:
    """Create an anonymous workbench workspace without SessionStore/Project records."""
    session_id = WORKBENCH_SESSION_PREFIX + uuid.uuid4().hex
    root = _session_dir(session_id)
    assert root is not None
    root.mkdir(parents=True, exist_ok=False)
    (root / "session.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "kind": "workbench",
                "created_at": time.time(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {"session_id": session_id, "storage": "workbench_local"}


def require_workbench_session(session_id: str) -> Tuple[Optional[Path], Optional[str]]:
    """Validate a workbench-only session; never consult AgentPlatform user/session tables."""
    root = _session_dir(session_id)
    if root is None:
        return None, "无效的工作台会话 ID，请新建工作区"
    if not root.is_dir():
        return None, "工作台会话不存在或已过期，请新建工作区"
    return root, None


def upload_workbench_file(session_id: str, filename: str, content: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Save a table file into the independent workbench workspace."""
    from utils.upload_naming import allocate_unique_name_in_dir, original_basename

    root, err = require_workbench_session(session_id)
    if err or root is None:
        return None, err
    client_original = original_basename(filename or "")
    suffix = Path(client_original).suffix.lower()
    if not client_original or suffix not in TABLE_EXTENSIONS:
        return None, "仅支持 CSV / Excel / TSV 数据文件"
    allocated = allocate_unique_name_in_dir(str(root), client_original)
    target = root / allocated.stored_name
    target.write_bytes(content)
    return {
        "session_id": session_id,
        "relative_path": allocated.stored_name,
        "original_filename": client_original,
        "renamed": allocated.renamed,
        "bytes": len(content),
        "storage": "workbench_local",
    }, None


def find_session_data_file(session_id: str, user_id: int) -> Tuple[Optional[Path], Optional[str]]:
    """Locate data in an independent workbench workspace.

    ``user_id`` remains in the function signature for orchestrator compatibility,
    but workbench files are deliberately not stored in AgentPlatform user sessions.
    """
    del user_id
    root, err = require_workbench_session(session_id)
    if err or root is None:
        return None, err

    candidates: List[Path] = []
    for p in sorted(root.iterdir()):
        if p.is_file() and p.suffix.lower() in TABLE_EXTENSIONS:
            candidates.append(p)

    if not candidates:
        return None, "工作区无 CSV/Excel 文件，请先上传"

    # Prefer data.csv / data.xlsx naming
    for c in candidates:
        if c.stem.lower().startswith("data"):
            return c, None
    return candidates[0], None


def run_sync_analysis(
    session_id: str,
    user_id: int,
    task: str,
    timeout: float = 300.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """同步执行完整 workbench 分析（供 chat 接入）。"""
    if not session_id.strip():
        return None, "session_id 不能为空"
    if not task.strip():
        return None, "任务描述不能为空"

    csv_path, err = find_session_data_file(session_id, user_id)
    if err:
        return None, err
    assert csv_path is not None

    run_id, run_dir = _ORCH.create_run_dir(session_id)
    state = WorkbenchRunState(
        run_id=run_id,
        session_id=session_id,
        user_id=user_id,
        task=task.strip(),
        run_dir=str(run_dir),
    )
    _RUNS[run_id] = state

    t0 = time.time()
    try:
        for _ in _ORCH.run_events(task.strip(), csv_path, session_id, user_id, state=state):
            if time.time() - t0 > timeout:
                state.cancel_event.set()
                state.status = "error"
                state.error = f"timeout after {timeout}s"
                break
    except Exception as exc:
        state.status = "error"
        state.error = str(exc)

    _try_persist_run(state, None)
    _persist_run_final(state)
    return format_run_result(state), None


def wait_for_run(run_id: str, timeout: float = 300.0, poll: float = 0.5) -> Tuple[Optional[WorkbenchRunState], Optional[str]]:
    """等待异步 run 完成。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _RUNS.get(run_id)
        if not state:
            return None, f"run_id 不存在: {run_id}"
        if state.status in ("completed", "error", "cancelled"):
            _persist_run_final(state)
            return state, None
        time.sleep(poll)
    return None, f"等待 run 超时 ({timeout}s)"


def format_run_result(state: WorkbenchRunState) -> Dict[str, Any]:
    """格式化 workbench 结果为 chat/API 统一结构。"""
    ok_steps = [s for s in state.steps if s.get("status") == "ok"]
    lines = [state.summary or ""]
    if state.evaluation:
        ev = state.evaluation
        lines.append(
            f"\n\n**结果评价**：完整度 {ev.get('completeness_score', '—')}，"
            f"算子链 {ev.get('step_count', len(state.steps))} 步。"
        )
        for flag in (ev.get("flags") or [])[:3]:
            lines.append(f"- {flag}")
    if state.charts:
        lines.append(f"\n\n**图表**：已生成 {len(state.charts)} 张（见 run 详情）。")
    if ok_steps:
        lines.append(
            "\n\n**执行算子**："
            + ", ".join(s.get("solver", "") for s in ok_steps[:12])
            + ("..." if len(ok_steps) > 12 else "")
        )
    response_text = "\n".join(lines).strip() or f"分析完成（{state.status}）"
    chart_items = []
    for c in state.charts[:40]:
        item = {
            k: v
            for k, v in c.items()
            if k in (
                "title", "path", "chart_type", "filename", "base64",
                "analysis", "analysis_source", "x", "y", "hue", "cols",
            )
        }
        if item.get("path") and not item.get("filename"):
            item["filename"] = Path(item["path"]).name
        # Prefer path download over embedding base64 (keeps API light)
        chart_items.append(item)
    return {
        "run_id": state.run_id,
        "route": state.route,
        "status": state.status,
        "response": response_text,
        "summary": state.summary,
        "evaluation": state.evaluation,
        "suggestions": state.suggestions,
        "step_count": len(state.steps),
        "chart_count": len(state.charts),
        "charts": chart_items,
        "steps": state.steps,
        "manifest_path": state.manifest_path,
        "error": state.error,
    }


def _persist_run_final(state: WorkbenchRunState) -> None:
    try:
        run_dir = Path(state.run_dir)
        if not run_dir.is_dir():
            return
        meta = format_run_result(state)
        (run_dir / "run_meta.json").write_text(json.dumps(_state_to_progress(state), ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "run_result.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def start_run(
    session_id: str,
    user_id: int,
    task: str,
    project_name: Optional[str] = None,
    auto_charts: bool = False,
    chart_specs: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not session_id.strip():
        return None, "session_id 不能为空"
    if not task.strip():
        return None, "任务描述不能为空"

    csv_path, err = find_session_data_file(session_id, user_id)
    if err:
        return None, err
    assert csv_path is not None

    run_id, run_dir = _ORCH.create_run_dir(session_id)
    state = WorkbenchRunState(
        run_id=run_id,
        session_id=session_id,
        user_id=user_id,
        task=task.strip(),
        run_dir=str(run_dir),
        auto_charts=bool(auto_charts),
        chart_specs=list(chart_specs or []),
    )
    _RUNS[run_id] = state

    def _worker():
        try:
            for _ in _ORCH.run_events(task.strip(), csv_path, session_id, user_id, state=state):
                pass
        except Exception as exc:
            state.status = "error"
            state.error = str(exc)

    t = threading.Thread(target=_worker, daemon=True)
    _RUN_THREADS[run_id] = t
    t.start()

    _try_persist_run(state, project_name)
    return {
        "run_id": run_id,
        "session_id": session_id,
        "status": "started",
        "data_file": str(csv_path),
        "task": task.strip(),
        "auto_charts": bool(auto_charts),
        "chart_specs_count": len(chart_specs or []),
    }, None


def get_progress(run_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    state = _RUNS.get(run_id)
    if not state:
        # Try load from disk
        loaded = _load_run_from_disk(run_id)
        if loaded:
            return loaded, None
        return None, f"run_id 不存在: {run_id}"
    return _state_to_progress(state), None


def cancel_run(run_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    state = _RUNS.get(run_id)
    if not state:
        return None, f"run_id 不存在: {run_id}"
    state.cancel_event.set()
    state.status = "cancelling"
    return {"run_id": run_id, "status": "cancelling"}, None


def modify_run(run_id: str, new_task: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    state = _RUNS.get(run_id)
    if not state:
        return None, f"run_id 不存在: {run_id}"
    state.cancel_event.set()
    time.sleep(0.3)
    return start_run(state.session_id, state.user_id, new_task)


def get_run_detail(run_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    state = _RUNS.get(run_id)
    if not state:
        disk = _load_run_result_from_disk(run_id)
        if disk:
            return disk, None
        loaded = _load_run_from_disk(run_id)
        if loaded:
            return loaded, None
        return None, f"run_id 不存在: {run_id}"
    prog = _state_to_progress(state)
    charts = []
    for c in state.charts[:40]:
        item = {
            k: v
            for k, v in c.items()
            if k in (
                "title", "path", "chart_type", "filename", "base64",
                "analysis", "analysis_source", "x", "y", "hue", "cols",
            )
        }
        if item.get("path") and not item.get("filename"):
            item["filename"] = Path(item["path"]).name
        charts.append(item)
    prog["charts"] = charts
    prog["summary"] = state.summary
    prog["evaluation"] = state.evaluation
    # Fallback: hydrate summary/eval from disk if in-memory empty
    if (not prog["summary"] or not prog["evaluation"]) and state.run_dir:
        rd = Path(state.run_dir)
        if not prog["summary"]:
            sm = rd / "summary.md"
            if sm.is_file():
                try:
                    prog["summary"] = sm.read_text(encoding="utf-8")
                except Exception:
                    pass
        if not prog["evaluation"]:
            evp = rd / "evaluation.json"
            if evp.is_file():
                try:
                    prog["evaluation"] = json.loads(evp.read_text(encoding="utf-8"))
                except Exception:
                    pass
    prog["suggestions"] = state.suggestions
    prog["manifest_path"] = state.manifest_path
    prog["run_dir"] = state.run_dir
    return prog, None


def list_runs(session_id: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    results = []
    for rid, st in _RUNS.items():
        if st.session_id == session_id:
            results.append(_state_to_progress(st))
    # Scan disk
    root = _runs_root() / session_id
    if root.is_dir():
        for d in sorted(root.iterdir(), reverse=True):
            if d.is_dir() and d.name not in {r.get("run_id") for r in results}:
                meta = d / "run_meta.json"
                if meta.is_file():
                    try:
                        results.append(json.loads(meta.read_text(encoding="utf-8")))
                    except Exception:
                        pass
    return results, None


def stream_run_events(run_id: str) -> Generator[str, None, None]:
    """SSE generator — replay timeline then poll until done."""
    state = _RUNS.get(run_id)
    if not state:
        yield _sse("error", {"message": f"run not found: {run_id}"})
        return

    sent = 0
    while True:
        timeline = state.timeline
        while sent < len(timeline):
            yield _sse(timeline[sent]["type"], {k: v for k, v in timeline[sent].items() if k != "type"})
            sent += 1
        if state.status in ("completed", "error", "cancelled"):
            yield _sse("done", {"run_id": run_id, "status": state.status})
            break
        time.sleep(0.4)


def _state_to_progress(state: WorkbenchRunState) -> Dict[str, Any]:
    return {
        "run_id": state.run_id,
        "session_id": state.session_id,
        "task": state.task,
        "route": state.route,
        "status": state.status,
        "current_stage": state.current_stage,
        "step_count": len(state.steps),
        "steps": state.steps[-20:],
        "chart_count": len(state.charts),
        "error": state.error,
        "timeline_len": len(state.timeline),
    }


def _sse(event_type: str, data: Dict[str, Any]) -> str:
    payload = json.dumps({"type": event_type, **data}, ensure_ascii=False, default=str)
    return f"data: {payload}\n\n"


def _try_persist_run(state: WorkbenchRunState, project_name: Optional[str]) -> None:
    try:
        from db.workbench_schema import ensure_workbench_tables
        from utils.mysql_utils import mysql_handler
        ensure_workbench_tables(mysql_handler)
        run_dir = Path(state.run_dir)
        meta = _state_to_progress(state)
        meta["project_name"] = project_name or ""
        (run_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_run_artifacts(run_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """读取 run 内 CSV 产物供前端表格/热图展示。"""
    import pandas as pd
    state = _RUNS.get(run_id)
    run_dir = Path(state.run_dir) if state and state.run_dir else None
    if not run_dir or not run_dir.is_dir():
        root = _runs_root()
        for session_dir in root.iterdir() if root.is_dir() else []:
            cand = session_dir / run_id
            if cand.is_dir():
                run_dir = cand
                break
    if not run_dir or not run_dir.is_dir():
        return None, f"run_id 不存在: {run_id}"

    out: Dict[str, Any] = {}
    pipe = run_dir / "pipeline_output"
    if pipe.is_dir():
        for csv_path in pipe.rglob("describe_full.csv"):
            try:
                df = pd.read_csv(csv_path)
                out["describe"] = {
                    "columns": list(df.columns),
                    "rows": df.head(50).to_dict(orient="records"),
                }
                break
            except Exception:
                pass
        for csv_path in pipe.rglob("*pearson*matrix*.csv"):
            try:
                mat = pd.read_csv(csv_path, index_col=0)
                labels = list(mat.columns)
                out["correlation"] = {
                    "labels": labels,
                    "matrix": mat.values.tolist(),
                }
                break
            except Exception:
                pass
    return out, None


def resolve_chart_path(run_id: str, filename: str) -> Tuple[Optional[Path], Optional[str]]:
    filename = Path(filename).name
    state = _RUNS.get(run_id)
    candidates = []
    if state and state.run_dir:
        candidates.append(Path(state.run_dir) / "charts" / filename)
    root = _runs_root()
    if root.is_dir():
        for session_dir in root.iterdir():
            p = session_dir / run_id / "charts" / filename
            candidates.append(p)
    for p in candidates:
        if p.is_file():
            return p, None
    return None, "chart not found"


def _load_run_from_disk(run_id: str) -> Optional[Dict[str, Any]]:
    root = _runs_root()
    if not root.is_dir():
        return None
    for session_dir in root.iterdir():
        candidate = session_dir / run_id / "run_meta.json"
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _load_run_result_from_disk(run_id: str) -> Optional[Dict[str, Any]]:
    root = _runs_root()
    if not root.is_dir():
        return None
    for session_dir in root.iterdir():
        candidate = session_dir / run_id / "run_result.json"
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _find_run_dir(run_id: str) -> Optional[Path]:
    state = _RUNS.get(run_id)
    if state and state.run_dir:
        p = Path(state.run_dir)
        if p.is_dir():
            return p
    root = _runs_root()
    if not root.is_dir():
        return None
    for session_dir in root.iterdir():
        cand = session_dir / run_id
        if cand.is_dir():
            return cand
    return None


def resume_from_step(
    parent_run_id: str,
    from_step: int,
    user_id: int,
    task_override: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """从父 run 的 from_step 断点续跑，生成新 run_id。"""
    parent_dir = _find_run_dir(parent_run_id)
    if not parent_dir:
        return None, f"父 run 不存在: {parent_run_id}"
    state = _RUNS.get(parent_run_id)
    session_id = state.session_id if state else parent_dir.parent.name
    owner = state.user_id if state else user_id
    if state and state.user_id != user_id:
        return None, "无权续跑该 run"

    plan_path = parent_dir / "plan.json"
    if not plan_path.is_file():
        return None, "父 run 缺少 plan.json，无法断点续跑"

    new_run_id, new_run_dir = _ORCH.create_run_dir(session_id)
    new_state = WorkbenchRunState(
        run_id=new_run_id,
        session_id=session_id,
        user_id=owner,
        task=task_override or (state.task if state else "resume analysis"),
        run_dir=str(new_run_dir),
    )
    _RUNS[new_run_id] = new_state

    def _worker():
        try:
            for _ in _ORCH.run_resume_events(
                parent_dir, int(from_step), session_id, owner, new_state, task_override
            ):
                pass
        except Exception as exc:
            new_state.status = "error"
            new_state.error = str(exc)
        _persist_run_final(new_state)

    t = threading.Thread(target=_worker, daemon=True)
    _RUN_THREADS[new_run_id] = t
    t.start()
    _try_persist_run(new_state, None)
    return {
        "run_id": new_run_id,
        "parent_run_id": parent_run_id,
        "from_step": int(from_step),
        "session_id": session_id,
        "status": "started",
    }, None


def record_export(
    session_id: str,
    user_id: int,
    run_id: Optional[str],
    kind: str,
    artifact_path: str = "",
    note: str = "",
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """写入导出台账（JSONL + 可选 DB）。"""
    import uuid
    from datetime import datetime, timezone

    if not session_id.strip():
        return None, "session_id 不能为空"
    kind = (kind or "artifact").strip()
    export_id = "exp_" + uuid.uuid4().hex[:12]
    entry = {
        "export_id": export_id,
        "session_id": session_id,
        "run_id": run_id or "",
        "kind": kind,
        "artifact_path": artifact_path,
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Session-level ledger
    sess_dir = _runs_root() / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    ledger = sess_dir / "exports.jsonl"
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if run_id:
        rd = _find_run_dir(run_id)
        if rd:
            run_ledger = rd / "exports.jsonl"
            with run_ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return entry, None


def list_exports(session_id: str, run_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not session_id.strip():
        return [], "session_id 不能为空"
    entries: List[Dict[str, Any]] = []
    if run_id:
        rd = _find_run_dir(run_id)
        path = (rd / "exports.jsonl") if rd else None
    else:
        path = _runs_root() / session_id / "exports.jsonl"
    if path and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    entries.reverse()
    return entries[:200], None


def export_run_bundle(
    run_id: str,
    session_id: str,
    user_id: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """打包摘要/评价/图表清单为导出记录（路径写入台账）。"""
    import shutil
    from datetime import datetime, timezone

    rd = _find_run_dir(run_id)
    if not rd:
        return None, f"run_id 不存在: {run_id}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    bundle_dir = rd / "exports" / f"bundle_{stamp}"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for name in ("summary.md", "evaluation.json", "manifest.json", "plan.json", "charts.json"):
        src = rd / name
        if src.is_file():
            shutil.copy2(src, bundle_dir / name)
    charts = rd / "charts"
    if charts.is_dir():
        dst = bundle_dir / "charts"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(charts, dst)

    entry, err = record_export(
        session_id, user_id, run_id,
        kind="bundle",
        artifact_path=str(bundle_dir),
        note="分析摘要+评价+图表打包导出",
    )
    if err:
        return None, err
    assert entry is not None
    entry["bundle_dir"] = str(bundle_dir)
    return entry, None
