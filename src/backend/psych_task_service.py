# backend/psych_task_service.py — 统一异步任务生命周期

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from db import psych_store as store
from psych.paths import new_id, task_artifact_dir

logger = logging.getLogger(__name__)

_CANCEL_FLAGS: Dict[str, threading.Event] = {}
_THREADS: Dict[str, threading.Thread] = {}


def submit_task(
    user_id: int,
    module: str,
    method_id: str,
    params: Dict[str, Any],
    worker: Callable[[str, Dict[str, Any]], Tuple[Dict[str, Any], Optional[str]]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    创建任务并在后台线程执行 worker(task_id, params) -> (result, error)。
    """
    task_id = new_id("task_")
    artifact = task_artifact_dir(user_id, task_id)
    params = dict(params or {})
    params["_artifact_dir"] = artifact
    params["_user_id"] = user_id

    _, ierr = store.insert_task(
        {
            "task_id": task_id,
            "user_id": user_id,
            "module": module,
            "method_id": method_id,
            "status": "pending",
            "params_json": params,
            "artifact_path": artifact,
        }
    )
    if ierr:
        return None, ierr

    cancel_flag = threading.Event()
    _CANCEL_FLAGS[task_id] = cancel_flag

    def _run():
        store.update_task(task_id, {"status": "running"})
        try:
            if cancel_flag.is_set():
                store.update_task(
                    task_id,
                    {
                        "status": "cancelled",
                        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
                return
            result, err = worker(task_id, params)
            if cancel_flag.is_set():
                store.update_task(
                    task_id,
                    {
                        "status": "cancelled",
                        "result_json": result or {},
                        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
                return
            if err:
                store.update_task(
                    task_id,
                    {
                        "status": "failed",
                        "error_message": err,
                        "result_json": result or {},
                        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
            else:
                store.update_task(
                    task_id,
                    {
                        "status": "success",
                        "result_json": result or {},
                        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
        except Exception as exc:
            logger.exception("psych task %s failed", task_id)
            store.update_task(
                task_id,
                {
                    "status": "failed",
                    "error_message": str(exc),
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
        finally:
            _CANCEL_FLAGS.pop(task_id, None)
            _THREADS.pop(task_id, None)

    t = threading.Thread(target=_run, daemon=True, name=f"psych-{task_id}")
    _THREADS[task_id] = t
    t.start()
    return {
        "task_id": task_id,
        "module": module,
        "method_id": method_id,
        "status": "pending",
        "artifact_path": artifact,
    }, None


def get_task(task_id: str, user_id: Optional[int] = None) -> Tuple[Optional[dict], Optional[str]]:
    row, err = store.get_task_by_task_id(task_id)
    if err:
        return None, err
    if not row:
        return None, f"任务不存在: {task_id}"
    if user_id is not None and int(row.get("user_id") or 0) != int(user_id):
        return None, "无权访问该任务"
    return row, None


def list_user_tasks(
    user_id: int, module: Optional[str] = None, limit: int = 50
) -> Tuple[List[dict], Optional[str]]:
    return store.list_tasks(user_id, module=module, limit=limit)


def cancel_task(task_id: str, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    row, err = get_task(task_id, user_id)
    if err:
        return None, err
    assert row is not None
    status = row.get("status")
    if status in ("success", "failed", "cancelled"):
        return {"task_id": task_id, "status": status, "message": "任务已结束"}, None
    flag = _CANCEL_FLAGS.get(task_id)
    if flag:
        flag.is_set() or flag.set()
    store.update_task(
        task_id,
        {
            "status": "cancelled",
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    return {"task_id": task_id, "status": "cancelled"}, None


def is_cancelled(task_id: str) -> bool:
    flag = _CANCEL_FLAGS.get(task_id)
    return bool(flag and flag.is_set())
