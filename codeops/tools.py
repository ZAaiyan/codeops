from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShellResult:
    returncode: int
    stdout: str
    stderr: str

    def to_text(self) -> str:
        return (
            f"returncode: {self.returncode}\n"
            f"stdout:\n{self.stdout}\n"
            f"stderr:\n{self.stderr}\n"
        )


def _workspace_root(root: Path | None) -> Path:
    return (root or Path.cwd()).resolve()


def _resolve_in_workspace(path: str, *, root: Path | None) -> Path:
    if path.strip() == "":
        raise ToolError("path is empty")
    if path.startswith("~"):
        raise ToolError("path must not use '~'")

    workspace = _workspace_root(root)
    p = Path(path)
    if not p.is_absolute():
        p = (workspace / p).resolve()
    else:
        p = p.resolve()

    if not p.is_relative_to(workspace):
        raise ToolError("path escapes workspace root")
    return p


def read_file(path: str, *, root: Path | None = None, max_bytes: int = 200_000) -> str:
    p = _resolve_in_workspace(path, root=root)
    if not p.exists():
        raise ToolError("file does not exist")
    if p.is_dir():
        raise ToolError("path is a directory")
    data = p.read_bytes()
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace")


def write_file(
    path: str,
    content: str,
    *,
    root: Path | None = None,
    require_confirm: bool = True,
    yes: bool = False,
) -> str:
    if require_confirm and not yes:
        raise ToolError("write requires confirmation")

    p = _resolve_in_workspace(path, root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    rel = p.relative_to(_workspace_root(root))
    return f"ok: wrote {len(content.encode('utf-8'))} bytes to {rel.as_posix()}"


def list_files(
    path: str = ".",
    *,
    root: Path | None = None,
    max_entries: int = 400,
) -> list[str]:
    p = _resolve_in_workspace(path, root=root)
    if not p.exists():
        raise ToolError("path does not exist")

    workspace = _workspace_root(root)

    entries: list[str] = []
    if p.is_file():
        return [p.relative_to(workspace).as_posix()]

    for child in sorted(p.rglob("*")):
        if len(entries) >= max_entries:
            break
        if child.is_dir():
            continue
        try:
            rel = child.relative_to(workspace).as_posix()
        except ValueError:
            continue
        entries.append(rel)
    return entries


_DANGEROUS_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|\s)sudo(\s|$)"), "sudo is not allowed"),
    (re.compile(r"(^|\s)mkfs(\.\w+)?(\s|$)"), "disk formatting is not allowed"),
    (re.compile(r"(^|\s)fdisk(\s|$)"), "disk partitioning is not allowed"),
    (re.compile(r"(^|\s)dd(\s|$)"), "raw disk operations are not allowed"),
    (re.compile(r"(^|\s)diskutil(\s+erase|(\s|$))"), "diskutil destructive ops are not allowed"),
    (re.compile(r"(^|\s)shutdown(\s|$)"), "shutdown is not allowed"),
    (re.compile(r"(^|\s)reboot(\s|$)"), "reboot is not allowed"),
    (re.compile(r"(^|\s)halt(\s|$)"), "halt is not allowed"),
    (re.compile(r"(^|\s)poweroff(\s|$)"), "poweroff is not allowed"),
]


def _validate_shell_command(command: str, *, root: Path | None) -> list[str]:
    if command.strip() == "":
        raise ToolError("command is empty")

    for pattern, reason in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            raise ToolError(f"blocked command: {reason}")

    try:
        tokens = shlex.split(command)
    except ValueError as e:
        raise ToolError(f"cannot parse command: {e}") from e

    if tokens and tokens[0] == "rm":
        for tok in tokens[1:]:
            if tok in {"-r", "-rf", "-fr", "--recursive"}:
                raise ToolError("blocked command: recursive delete is not allowed")
            if tok.startswith("-") and "r" in tok and tok != "-f":
                raise ToolError("blocked command: recursive delete is not allowed")

    workspace = _workspace_root(root)
    for tok in tokens:
        if tok == ".." or tok.startswith("../") or "/../" in tok:
            raise ToolError("blocked command: path traversal is not allowed")
        if tok.startswith("~"):
            raise ToolError("blocked command: '~' paths are not allowed")
        if tok.startswith("/"):
            p = Path(tok).resolve()
            if not p.is_relative_to(workspace):
                raise ToolError("blocked command: absolute paths outside workspace are not allowed")

    return tokens


def run_shell(command: str, *, root: Path | None = None, timeout_s: int = 60) -> ShellResult:
    _validate_shell_command(command, root=root)
    workspace = _workspace_root(root)

    env = os.environ.copy()
    env["PAGER"] = "cat"

    completed = subprocess.run(
        command,
        shell=True,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=env,
    )
    return ShellResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
