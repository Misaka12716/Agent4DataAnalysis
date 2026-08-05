import json
import os
from datetime import datetime
from typing import Any, Literal

from configs.config import TEMP_FOLDER, ENABLE_MODEL_LOG


# 会话/编排调试日志与进程日志（backend.log）分开，避免混在同一目录
LOG_FOLDER = os.path.join(TEMP_FOLDER, "logs", "sessions")

EventKind = Literal["llm", "phase_start", "phase_end", "milestone"]

_KIND_LABEL = {
    "llm": "LLM 交互",
    "phase_start": "阶段开始",
    "phase_end": "阶段结束",
    "milestone": "里程碑",
}


def _ensure_log_dir() -> None:
    """
    确保日志目录存在。
    出现异常（权限、磁盘问题等）时静默失败，避免影响主流程。
    """
    try:
        os.makedirs(LOG_FOLDER, exist_ok=True)
    except Exception:
        pass


def _get_log_file_path(dialogue_id: str) -> str:
    """
    根据对话 ID 获取日志文件路径。
    若对话 ID 为空，则归入 'unknown' 文件。
    """
    safe_id = dialogue_id or "unknown"
    safe_id = "".join(c for c in safe_id if c.isalnum() or c in ("-", "_"))
    if not safe_id:
        safe_id = "unknown"
    filename = f"{safe_id}.log"
    return os.path.join(LOG_FOLDER, filename)


def _serialize_content(content: Any) -> str:
    """将任意内容转为适合写入日志的字符串（dict/list 用 JSON，长文本用围栏）。"""
    if content is None:
        return "（无）\n"
    if isinstance(content, (dict, list)):
        try:
            return json.dumps(content, ensure_ascii=False, indent=2) + "\n"
        except (TypeError, ValueError):
            return str(content) + "\n"
    text = str(content)
    if not text.strip():
        return "（空）\n"
    if len(text) > 1600 or "\n" in text:
        return "```\n" + text.rstrip() + "\n```\n"
    return text.rstrip() + "\n"


def _format_markdown_entry(
    ts: str,
    dialogue_id: str,
    stage: str,
    event_kind: EventKind,
    body: str,
) -> str:
    """
    单条日志：分隔线 + 标题 + 元信息 + 正文，便于按会话阅读（类 Markdown 层次）。
    """
    did = dialogue_id or "unknown"
    kind_zh = _KIND_LABEL.get(event_kind, event_kind)
    lines = [
        "",
        "================================================================================",
        f"### {ts} · 会话 `{did}`",
        "",
        f"- **类型：** {kind_zh}",
        f"- **标识：** `{stage}`",
        "",
        "---",
        "",
        body.rstrip(),
        "",
    ]
    return "\n".join(lines)


def log_model_event(
    dialogue_id: str,
    stage: str,
    content: Any,
    *,
    event_kind: EventKind = "llm",
) -> None:
    """
    记录编排/大模型各阶段事件。

    - event_kind=`llm`（默认）：记录模型输入、输出或中间结果（与原先用法兼容）。
    - `phase_start` / `phase_end`：阶段起止，content 可为 str 或 dict（摘要）。
    - `milestone`：子步骤或检查点说明。

    日志为分段 Markdown 风格，便于在编辑器中高亮或折叠阅读。
    """
    if not ENABLE_MODEL_LOG:
        return

    _ensure_log_dir()

    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if event_kind == "llm":
            inner = (
                "#### 正文\n\n"
                + _serialize_content(content).rstrip()
            )
        else:
            inner = "#### 详情\n\n" + _serialize_content(content).rstrip()

        block = _format_markdown_entry(ts, dialogue_id, stage, event_kind, inner)
        log_path = _get_log_file_path(dialogue_id)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(block + "\n")
    except Exception:
        pass


def log_phase_start(dialogue_id: str, stage: str, detail: Any = None) -> None:
    """记录某阶段开始（可选 detail 为 str / dict）。"""
    log_model_event(dialogue_id, stage, detail, event_kind="phase_start")


def log_phase_end(dialogue_id: str, stage: str, detail: Any = None) -> None:
    """记录某阶段结束。"""
    log_model_event(dialogue_id, stage, detail, event_kind="phase_end")


def log_milestone(dialogue_id: str, stage: str, detail: Any = None) -> None:
    """记录阶段内子步骤或检查点。"""
    log_model_event(dialogue_id, stage, detail, event_kind="milestone")
