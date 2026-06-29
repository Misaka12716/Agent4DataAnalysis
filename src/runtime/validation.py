from __future__ import annotations

import os
import re
import shlex
from typing import Optional, Tuple

from runtime.config import get_runner_python
from utils.workspace_manager import is_safe_relative_path, resolve_workspace_root, to_absolute_path

_SHELL_METACHAR_RE = re.compile(r"[;|&`$<>]")
_PYTHON3_CMD_RE = re.compile(r"^python3\s+(\S+)\s*$")


class ValidationError(ValueError):
    pass


def resolve_safe_absolute_path_in_workdir(workdir: str, relative_path: str) -> Optional[str]:
    """基于已知 workdir 校验相对路径并返回绝对路径（不查 DB）。"""
    if not is_safe_relative_path(relative_path):
        return None
    root = os.path.abspath(workdir)
    abs_path = os.path.normpath(os.path.join(root, relative_path.replace("/", os.sep)))
    try:
        root_real = os.path.realpath(root)
        abs_real = os.path.realpath(abs_path)
    except OSError:
        return None
    if abs_real != root_real and not abs_real.startswith(root_real + os.sep):
        return None
    return abs_real


def resolve_safe_absolute_path(session_id: str, relative_path: str) -> Optional[str]:
    """校验相对路径并返回工作区内的绝对路径；不安全或逃逸时返回 None。"""
    if not is_safe_relative_path(relative_path):
        return None
    abs_path = to_absolute_path(session_id, relative_path)
    if not abs_path:
        return None
    root = resolve_workspace_root(session_id)
    if not root:
        return None
    try:
        root_real = os.path.realpath(root)
        abs_real = os.path.realpath(abs_path)
    except OSError:
        return None
    if abs_real != root_real and not abs_real.startswith(root_real + os.sep):
        return None
    return abs_real


def validate_python_command(
    cmd: str, session_id: str, workdir: Optional[str] = None
) -> Tuple[str, str]:
    """
    解析并校验 python3 执行命令。
    返回 (python_executable, safe_relative_script_path)。
    """
    cmd = (cmd or "").strip()
    if not cmd:
        raise ValidationError("命令不能为空")
    if _SHELL_METACHAR_RE.search(cmd):
        raise ValidationError("命令包含不允许的 shell 元字符")

    match = _PYTHON3_CMD_RE.match(cmd)
    if not match:
        raise ValidationError("仅允许 python3 <相对路径.py> 形式的命令")

    rel_script = match.group(1).strip().strip("'\"")
    if not rel_script.endswith(".py"):
        raise ValidationError("脚本必须是 .py 文件")

    abs_script = (
        resolve_safe_absolute_path_in_workdir(workdir, rel_script)
        if workdir
        else resolve_safe_absolute_path(session_id, rel_script)
    )
    if not abs_script or not os.path.isfile(abs_script):
        raise ValidationError(f"脚本不存在或路径不安全: {rel_script}")

    python_bin = get_runner_python()
    return python_bin, rel_script


def build_python_command(relative_script: str) -> str:
    rel = (relative_script or "").strip()
    if not rel.endswith(".py"):
        rel = rel.rstrip("/") + ".py"
    return f"python3 {shlex.quote(rel)}"
