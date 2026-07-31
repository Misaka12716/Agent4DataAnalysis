# utils/session_memory.py
# 会话级 SESSION_MEMORY.md：压缩认知模型，供各智能体提示词引用（详见 CubeSandbox/hypervisor/docs/agent-session-memory.md）。

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from configs.config import (
    MAX_CODER_CORRECTIONS,
    MAX_SUPERVISOR_INVOCATIONS,
    SESSION_MEMORY_ENABLED,
    SESSION_MEMORY_PROMPT_MAX_CHARS,
)
from db.session_store import SessionStore
from reader.agent import run_workspace_reader_with_markdown_sync
from reader.file_types import IMAGE_EXTENSIONS, TABLE_EXTENSIONS, TEXT_EXTENSIONS
from reader.formatters import workspace_digest_to_markdown
from utils.workspace_manager import list_workspace_files, resolve_workspace_root

logger = logging.getLogger(__name__)

SESSION_MEMORY_FILENAME = "SESSION_MEMORY.md"

_MAX_DIGEST_CHARS = 12000
_MAX_WORKER_SECTION = 10000
_MAX_PLAN_SECTION = 8000


def session_memory_enabled() -> bool:
    return bool(SESSION_MEMORY_ENABLED)


def _truncate(text: str, max_len: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 20] + "\n…（已截断）"


def _workspace_abs(session_id: str) -> Optional[str]:
    p = SessionStore.get_workspace_path(session_id)
    if p and str(p).strip():
        return str(p).strip()
    return resolve_workspace_root(session_id)


def read_session_memory_raw(session_id: str) -> str:
    """读取工作区根目录下 SESSION_MEMORY.md 全文；不存在则返回空字符串。"""
    if not session_id or not session_memory_enabled():
        return ""
    root = _workspace_abs(session_id)
    if not root:
        return ""
    path = os.path.join(root, SESSION_MEMORY_FILENAME)
    try:
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _strip_digest_section(text: str) -> str:
    """
    剥离「## 4. 数据与 Schema 摘要」段，避免与 file_info / workspace_context 重复注入。
    按下一节「## N.」或文件尾结束；找不到 §4 则原样返回。
    """
    t = text or ""
    m = re.search(
        r"^##\s*4\.\s*[^\n]*\n",
        t,
        flags=re.MULTILINE,
    )
    if not m:
        return t
    start = m.start()
    rest = t[m.end() :]
    m2 = re.search(r"^##\s*\d+\.\s+", rest, flags=re.MULTILINE)
    end = m.end() + m2.start() if m2 else len(t)
    return (t[:start] + t[end:]).strip()


def read_session_memory_for_prompt(session_id: str) -> str:
    """供注入提示词的摘录（剥离 §4 digest，并有长度上限）。"""
    raw = read_session_memory_raw(session_id)
    return _truncate(_strip_digest_section(raw), SESSION_MEMORY_PROMPT_MAX_CHARS)


def format_memory_for_prompt(excerpt: str, lang: str) -> str:
    """将摘录格式化为可拼入用户提示的可选块（空则返回空字符串）。"""
    text = (excerpt or "").strip()
    if not text:
        return ""
    text = _strip_digest_section(text)
    text = _truncate(text, SESSION_MEMORY_PROMPT_MAX_CHARS)
    if not text:
        return ""
    if lang == "zh":
        return f"\n\n【会话记忆 SESSION_MEMORY.md 摘录】\n{text}"
    return f"\n\n[SESSION_MEMORY.md excerpt]\n{text}"


def _classify_files(
    names: List[str],
) -> tuple[List[str], List[str], List[str], List[str], List[str]]:
    data, images, text_files, code, other = [], [], [], [], []
    for n in names:
        low = n.lower()
        _, ext = os.path.splitext(low)
        if ext in TABLE_EXTENSIONS:
            data.append(n)
        elif ext in IMAGE_EXTENSIONS:
            images.append(n)
        elif ext in TEXT_EXTENSIONS:
            text_files.append(n)
        elif ext == ".py":
            code.append(n)
        elif low.startswith("data"):
            data.append(n)
        else:
            other.append(n)
    return data, images, text_files, code, other


def _workspace_digest(workspace_abs: str, session_id: str = "") -> str:
    try:
        digest, md = run_workspace_reader_with_markdown_sync(
            workspace_abs, session_id=session_id
        )
        text = md.strip() or workspace_digest_to_markdown(digest).strip()
    except Exception:
        return "（无法生成工作区文件摘要）"
    return _truncate(text, _MAX_DIGEST_CHARS)


def _worker_section(worker_results: Optional[Dict[str, Any]]) -> str:
    if not worker_results:
        return "（尚未执行 Worker）"
    lines: List[str] = []
    ok = bool(worker_results.get("success"))
    lines.append(f"- 整体成功: {ok}")
    for msg in worker_results.get("error_messages") or []:
        if msg:
            lines.append(f"- 汇总错误: {_truncate(str(msg), 2000)}")
    for r in worker_results.get("results") or []:
        rel = r.get("relative_path", "")
        rc = r.get("returncode", "")
        succ = r.get("success", False)
        out = _truncate((r.get("stdout") or "").strip(), 2500)
        err = _truncate((r.get("stderr") or "").strip(), 2500)
        lines.append(f"- 文件 `{rel}`: success={succ}, returncode={rc}")
        if out:
            lines.append(f"  - stdout 摘录:\n```\n{out}\n```")
        if err:
            lines.append(f"  - stderr 摘录:\n```\n{err}\n```")
    logs = (worker_results.get("logs") or "").strip()
    if logs:
        lines.append(f"- 聚合日志摘录:\n```\n{_truncate(logs, 4000)}\n```")
    return _truncate("\n".join(lines), _MAX_WORKER_SECTION)


def _orchestration_section(trace: Optional[List[Dict[str, Any]]]) -> str:
    if not trace:
        return "（尚无）"
    rows = []
    for t in trace[-8:]:
        nxt = t.get("next", "")
        rsn = _truncate(str(t.get("reason", "")), 400)
        fb = _truncate(str(t.get("feedback", "")), 400)
        inv = t.get("invoke", "")
        rows.append(f"- 第 {inv} 次 supervisor → **{nxt}**；理由: {rsn}")
        if fb:
            rows.append(f"  - 反馈摘录: {fb}")
    return "\n".join(rows)


def build_session_memory_markdown(
    *,
    session_id: str,
    workspace_abs: Optional[str],
    lang: str,
    session_title: Optional[str],
    input_data: str,
    plan_data: Optional[Dict[str, Any]],
    planner_summary: str,
    requirement_analysis: str,
    steps_outline: str,
    workspace_context: Optional[Dict[str, Any]],
    code_file_paths: List[str],
    coder_results: Optional[List[Dict[str, Any]]],
    correction_attempts: int,
    last_coder_mode: str,
    worker_results: Optional[Dict[str, Any]],
    memory_trace: Optional[List[Dict[str, Any]]],
    reporter_done: bool,
    report_excerpt: str,
    streaming_status: str,
    open_issues: str,
    last_event: str,
    pipeline_note: str,
) -> str:
    """生成完整的 SESSION_MEMORY.md 正文。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws = workspace_abs or "（未知）"
    title_line = (session_title or "").strip() or "（未设置）"

    files = list((workspace_context or {}).get("file_list") or [])
    if not files and session_id:
        files = list_workspace_files(session_id)
    data_f, images_f, text_f, code_f, other_f = _classify_files(files)

    pd = plan_data if isinstance(plan_data, dict) else None
    ra = (pd.get("需求解析") if pd else "") or requirement_analysis
    so = (pd.get("步骤分解") if pd else "") or steps_outline
    ra = _truncate((ra or "").strip(), _MAX_PLAN_SECTION)
    so = _truncate((so or "").strip(), _MAX_PLAN_SECTION)
    ps = _truncate((planner_summary or "").strip(), _MAX_PLAN_SECTION)

    # 优先复用 state 内已有 digest，避免每次 persist 重跑完整 Reader。
    digest = ""
    if workspace_context and workspace_context.get("workspace_digest"):
        try:
            wd = workspace_context.get("workspace_digest")
            digest = _truncate(
                workspace_digest_to_markdown(wd) if isinstance(wd, dict) else str(wd),
                _MAX_DIGEST_CHARS,
            )
        except Exception:
            digest = "（workspace_digest 不可用）"
    elif workspace_context and workspace_context.get("excel_schema"):
        try:
            digest = _truncate(
                json.dumps(workspace_context.get("excel_schema"), ensure_ascii=False, default=str, indent=2),
                _MAX_DIGEST_CHARS,
            )
        except Exception:
            digest = "（schema 不可用）"
    else:
        root = resolve_workspace_root(session_id)
        if root:
            digest = _workspace_digest(root, session_id=session_id)

    paths = code_file_paths or ["main.py"]
    cr = coder_results or []
    mode_zh = last_coder_mode or "—"
    coder_lines = []
    for r in cr:
        coder_lines.append(
            f"- `{r.get('relative_path')}` 写入成功={r.get('success')} "
            f"{('错误: ' + str(r.get('error'))) if r.get('error') else ''}"
        )
    if not coder_lines:
        coder_lines.append("（尚无写入记录）")

    rep_ex = _truncate((report_excerpt or "").strip(), 3000)
    status_report = "已完成" if reporter_done else "未完成"
    if rep_ex:
        status_report += f"；摘录:\n```\n{rep_ex}\n```"

    md = f"""# 会话记忆 (Session Memory)

> 自动维护的压缩摘要，对应流水线状态与工作区现状；长文本已截断。  
> 最后更新: {now} · 事件: {last_event or "—"} · 流式状态: {streaming_status or "—"}

## 1. 会话元数据

| 字段 | 值 |
|------|-----|
| session_id | `{session_id}` |
| 工作区路径 | `{ws}` |
| 语言 | {lang} |
| 会话标题 | {title_line} |
| Supervisor 上限参考 | {MAX_SUPERVISOR_INVOCATIONS} 次 |
| Coder 修正上限参考 | {MAX_CODER_CORRECTIONS} 次 |

## 2. 用户目标与输入

{pipeline_note}

**当前轮用户输入摘要**

{_truncate((input_data or "").strip(), 4000)}

## 3. 工作区清单

**根目录文件（相对路径）:** {", ".join(files) if files else "（空）"}

- **数据文件:** {", ".join(data_f) if data_f else "—"}
- **图片:** {", ".join(images_f) if images_f else "—"}
- **文本:** {", ".join(text_f) if text_f else "—"}
- **代码文件:** {", ".join(code_f) if code_f else "—"}
- **其他:** {", ".join(other_f) if other_f else "—"}

## 4. 数据与 Schema 摘要

{digest or "（无）"}

## 5. 规划状态

**需求解析**

{ra or "（尚无）"}

**步骤分解**

{so or "（尚无）"}

**规划摘要 (planner_summary)**

{ps or "（尚无）"}

## 6. 代码与修正

- **目标入口:** {", ".join(paths)}
- **最近一次模式:** {mode_zh}
- **修正次数:** {correction_attempts}

{chr(10).join(coder_lines)}

## 7. 执行履历与错误账本

{_worker_section(worker_results)}

## 8. 编排决策摘要（最近至多 8 条）

{_orchestration_section(memory_trace)}

## 9. 报告与结论

- **Reporter 状态:** {status_report}

## 10. 开放问题与假设

{(open_issues or "（无）").strip() or "（无）"}

## 11. 轻量时间线

（详见服务器编排日志与 session_content；此处仅记录本轮压缩视图。）

---
*文件路径（相对工作区根）: `{SESSION_MEMORY_FILENAME}`*
"""
    return md


def _atomic_write(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".session_memory_", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_session_memory_file(session_id: str, markdown: str) -> None:
    """将会话记忆写入工作区根目录 SESSION_MEMORY.md。"""
    if not session_id or not session_memory_enabled():
        return
    root = _workspace_abs(session_id)
    if not root or not os.path.isdir(root):
        logger.warning("session memory skip write: no workspace for session_id=%s", session_id)
        return
    path = os.path.join(root, SESSION_MEMORY_FILENAME)
    try:
        _atomic_write(path, markdown)
    except OSError:
        logger.exception("session memory write failed: %s", path)


def _session_title(session_id: str) -> Optional[str]:
    row, err = SessionStore.get_session_user(session_id)
    if err or not row:
        return None
    t = (row.get("title") or "").strip()
    return t or None


def persist_from_pipeline_state(
    state: Dict[str, Any],
    *,
    report_excerpt: str = "",
    streaming_status: str = "",
    last_event: str = "",
    pipeline_note: str = "",
    open_issues: str = "",
) -> None:
    """
    根据 LangGraph 流水线状态刷新 SESSION_MEMORY.md。
    任意异常仅记录日志，不抛出，以免中断编排。
    """
    if not session_memory_enabled():
        return
    session_id = (state.get("session_id") or "").strip()
    if not session_id:
        return
    try:
        ws_abs = _workspace_abs(session_id)
        lang = (state.get("lang") or "zh").strip() or "zh"
        md = build_session_memory_markdown(
            session_id=session_id,
            workspace_abs=ws_abs,
            lang=lang,
            session_title=_session_title(session_id),
            input_data=str(state.get("input_data") or ""),
            plan_data=state.get("plan_data"),
            planner_summary=str(state.get("planner_summary") or ""),
            requirement_analysis=str(state.get("requirement_analysis") or ""),
            steps_outline=str(state.get("steps_outline") or ""),
            workspace_context=state.get("workspace_context") or {},
            code_file_paths=list(state.get("code_file_paths") or ["main.py"]),
            coder_results=state.get("coder_results") or [],
            correction_attempts=int(state.get("correction_attempts") or 0),
            last_coder_mode=str(state.get("last_coder_mode") or ""),
            worker_results=state.get("worker_results"),
            memory_trace=state.get("memory_trace") or [],
            reporter_done=bool(state.get("reporter_done")),
            report_excerpt=report_excerpt,
            streaming_status=streaming_status,
            open_issues=open_issues,
            last_event=last_event,
            pipeline_note=pipeline_note or "（本轮分析进行中或尚未开始）",
        )
        write_session_memory_file(session_id, md)
    except Exception:
        logger.exception("persist_from_pipeline_state failed: session_id=%s", session_id)


def persist_workspace_snapshot(
    session_id: str,
    *,
    lang: str = "zh",
    note: str = "",
    input_hint: str = "",
) -> None:
    """创建会话、上传文件等非流水线场景：仅刷新元数据与工作区现状。"""
    if not session_memory_enabled() or not session_id:
        return
    try:
        ws_abs = _workspace_abs(session_id)
        wc: Dict[str, Any] = {}
        root = resolve_workspace_root(session_id)
        if root:
            wc["file_list"] = list_workspace_files(session_id)
            try:
                from reader.agent import run_workspace_reader_sync
                from reader.legacy import excel_schema_from_digest

                wd = run_workspace_reader_sync(root, session_id=session_id)
                wc["workspace_digest"] = wd
                wc["excel_schema"] = excel_schema_from_digest(wd)
            except Exception:
                wc["workspace_digest"] = {}
                wc["excel_schema"] = {}
        empty: Dict[str, Any] = {
            "session_id": session_id,
            "input_data": input_hint or "（尚未发起本轮分析或见历史会话内容）",
            "lang": lang,
            "workspace_context": wc,
            "code_file_paths": ["main.py"],
            "coder_results": [],
            "correction_attempts": 0,
            "last_coder_mode": "",
            "plan_data": None,
            "planner_summary": "",
            "requirement_analysis": "",
            "steps_outline": "",
            "worker_results": None,
            "memory_trace": [],
            "reporter_done": False,
        }
        pipeline_note = note or "工作区快照（流水线未运行或空闲）。"
        persist_from_pipeline_state(
            empty,
            streaming_status="idle",
            last_event="workspace_snapshot",
            pipeline_note=pipeline_note,
        )
    except Exception:
        logger.exception("persist_workspace_snapshot failed: session_id=%s", session_id)