from __future__ import annotations

import os
import subprocess

from runtime.config import DEFAULT_COMMAND_TIMEOUT, MAX_OUTPUT_CHARS
from runtime.types import CommandResult
from runtime.validation import ValidationError, validate_python_command


class LocalCommands:
    def __init__(self, session_id: str, workdir: str) -> None:
        self._session_id = session_id
        self._workdir = workdir

    def run(
        self,
        cmd: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        run_cwd = cwd or self._workdir
        timeout_sec = float(timeout if timeout is not None else DEFAULT_COMMAND_TIMEOUT)
        try:
            python_bin, rel_script = validate_python_command(
                cmd, self._session_id, workdir=self._workdir
            )
            abs_script = os.path.join(self._workdir, rel_script.replace("/", os.sep))
            result = subprocess.run(
                [python_bin, abs_script],
                cwd=run_cwd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            stdout = (result.stdout or "")[:MAX_OUTPUT_CHARS]
            stderr = (result.stderr or "")[:MAX_OUTPUT_CHARS]
            return CommandResult(stdout=stdout, stderr=stderr, exit_code=result.returncode)
        except ValidationError as exc:
            return CommandResult(stdout="", stderr=str(exc), exit_code=-1)
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="执行超时", exit_code=-1)
        except Exception as exc:
            return CommandResult(stdout="", stderr=str(exc), exit_code=-1)
