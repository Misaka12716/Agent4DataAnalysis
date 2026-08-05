from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def returncode(self) -> int:
        return self.exit_code

    @property
    def success(self) -> bool:
        return self.exit_code == 0


@dataclass
class WriteInfo:
    path: str
    bytes_written: int


@runtime_checkable
class FilesystemAPI(Protocol):
    def write(self, path: str, data: bytes | str) -> WriteInfo | None: ...
    def read(self, path: str, format: Literal["bytes", "text"] = "bytes") -> bytes | str | None: ...
    def exists(self, path: str) -> bool: ...
    def list(self, dir_path: str = ".", depth: int = 1) -> list[str]: ...
    def delete(self, path: str) -> bool: ...


@runtime_checkable
class CommandsAPI(Protocol):
    def run(self, cmd: str, cwd: str | None = None, timeout: float | None = None) -> CommandResult: ...


@runtime_checkable
class ExecutionRuntime(Protocol):
    session_id: str
    workdir: str
    backend: str
    files: FilesystemAPI
    commands: CommandsAPI

    def close(self) -> None: ...
