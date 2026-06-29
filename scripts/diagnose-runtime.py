#!/usr/bin/env python3
"""本地 Runtime 诊断：工作区路径、write/read、Runner Python 示例执行。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CUBE_SANDBOX_ENABLED", "0")

from runtime.config import get_runner_python  # noqa: E402
from runtime.local.runtime import LocalRuntime  # noqa: E402
from utils.workspace_manager import init_workspace  # noqa: E402


def main() -> int:
    sid = (sys.argv[1] if len(sys.argv) > 1 else f"diag-{os.getpid()}").strip()
    user_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    root = init_workspace(user_id, sid)
    print(f"[runtime] workspace_root={root}")
    print(f"[runtime] exists={os.path.isdir(root)}")
    print(f"[runtime] runner_python={get_runner_python()}")

    rt = LocalRuntime(sid, root)
    print(f"[runtime] backend={rt.backend}")
    print(f"[runtime] workdir={rt.workdir}")

    info = rt.files.write("_diag_probe.txt", "runtime probe ok\n")
    print(f"[runtime] write={info}")

    rt.files.write("_diag_run.py", "print('python3_ok')\n")
    result = rt.commands.run("python3 _diag_run.py", cwd=rt.workdir, timeout=10.0)
    print(f"[runtime] run exit_code={result.exit_code}")
    print(f"[runtime] stdout={result.stdout.strip()!r}")
    if not result.success:
        print(f"[runtime] stderr={result.stderr!r}", file=sys.stderr)
        return 1

    print("[runtime] OK — 本地 Runtime 可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
