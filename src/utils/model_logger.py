import json
import os
from datetime import datetime
from typing import Any

from utils.config import TEMP_FOLDER, ENABLE_MODEL_LOG


LOG_FOLDER = os.path.join(TEMP_FOLDER, "logs")


def _ensure_log_dir() -> None:
    """
    确保日志目录存在。
    出现异常（权限、磁盘问题等）时静默失败，避免影响主流程。
    """
    try:
        os.makedirs(LOG_FOLDER, exist_ok=True)
    except Exception:
        # 日志目录创建失败时不影响主流程
        pass


def _get_log_file_path(dialogue_id: str) -> str:
    """
    根据对话 ID 获取日志文件路径。
    若对话 ID 为空，则归入 'unknown' 文件。
    """
    safe_id = dialogue_id or "unknown"
    # 简单清洗，避免特殊字符影响文件名
    safe_id = "".join(c for c in safe_id if c.isalnum() or c in ("-", "_"))
    if not safe_id:
        safe_id = "unknown"
    filename = f"{safe_id}.log"
    return os.path.join(LOG_FOLDER, filename)


def log_model_event(dialogue_id: str, stage: str, content: Any) -> None:
    """
    记录大模型交互各阶段的输入/输出/中间结果。

    日志格式：
        timestamp | stage | dialogue_id | json_or_str
    """
    if not ENABLE_MODEL_LOG:
        return

    _ensure_log_dir()

    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            serialized = json.dumps(content, ensure_ascii=False)
        except Exception:
            serialized = str(content)

        line = f"{ts} | {stage} | {dialogue_id or 'unknown'} | {serialized}\n"
        log_path = _get_log_file_path(dialogue_id)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # 文件权限、磁盘空间不足等异常不应影响主流程
        pass

