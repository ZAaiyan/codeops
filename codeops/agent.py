from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codeops.llm import chat_completion
from codeops.memory_store import MemoryStore
from codeops.tools import ToolError, list_files, read_file, run_shell, write_file


@dataclass(frozen=True)
class AgentConfig:
    model: str
    temperature: float = 0.0
    max_iterations: int = 12
    planner_model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    workspace_root: Path | None = None
    yes: bool = False
    debug: bool = False
    max_context_chars: int = 60_000
    memory_store: MemoryStore | None = None


@dataclass(frozen=True)
class AgentStep:
    iteration: int
    thought: str
    action: str
    args: dict[str, Any]


_SYSTEM_PROMPT = """You are CodeOps, a local CLI coding agent.

You MUST respond with a single JSON object and nothing else.

Schema:
{
  "thought": "short reasoning",
  "action": "read_file" | "write_file" | "list_files" | "run_shell" | "finish",
  "args": { ... },
  "final": false
}

Tool actions:
- read_file: { "path": "relative/path" }
- write_file: { "path": "relative/path", "content": "..." }
- list_files: { "path": "relative/path", "max_entries": 200 }
- run_shell: { "command": "..." }
- finish: { "result": "final answer to the user" } with final=true

Rules:
- Use tools whenever you need file contents, directory listing, or command output.
- Do not invent file contents.
- Keep thought short.
"""

_PLANNER_PROMPT = """You are CodeOps Planner.

Return a single JSON array of strings, and nothing else.

Each item is one concrete step that can be executed using tools (read_file/list_files/run_shell/write_file) if needed.

Rules:
- Keep steps short and action-oriented.
- Prefer safe, incremental changes.
- Include a final verification step (tests/build) when applicable.
"""


def _trim_messages(messages: list[dict[str, Any]], *, max_chars: int) -> list[dict[str, Any]]:
    if max_chars <= 0:
        return messages
    total = 0
    kept: list[dict[str, Any]] = []
    for msg in reversed(messages):
        total += len(str(msg.get("content", "")))
        kept.append(msg)
        if total >= max_chars:
            break
    return list(reversed(kept))


def _resolve_in_workspace(root: Path | None, rel_path: str) -> Path:
    workspace = (root or Path.cwd()).resolve()
    p = Path(rel_path)
    if p.is_absolute():
        raise ValueError("path must be relative")
    abs_p = (workspace / p).resolve()
    if not abs_p.is_relative_to(workspace):
        raise ValueError("path escapes workspace root")
    return abs_p


class Agent:
    def __init__(self, *, config: AgentConfig) -> None:
        self.config = config

    def plan(self, *, user_task: str, messages: list[dict[str, Any]]) -> list[str]:
        working: list[dict[str, Any]] = [{"role": "system", "content": _PLANNER_PROMPT}]
        working.extend(_trim_messages(messages, max_chars=self.config.max_context_chars))
        working.append({"role": "user", "content": user_task})
        raw = chat_completion(
            messages=working,
            model=self.config.planner_model or self.config.model,
            temperature=0.0,
            base_url=self.config.base_url,
            api_key=self.config.api_key,
        )
        try:
            parsed = json.loads(raw.strip())
        except Exception:
            return []
        if not isinstance(parsed, list):
            return []
        out: list[str] = []
        for item in parsed:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    out.append(s)
        return out[:12]

    def run(self, *, user_task: str, messages: list[dict[str, Any]]) -> tuple[str, list[AgentStep]]:
        working_messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
        working_messages.extend(_trim_messages(messages, max_chars=self.config.max_context_chars))
        working_messages.append({"role": "user", "content": user_task})

        steps: list[AgentStep] = []

        for i in range(self.config.max_iterations):
            raw = chat_completion(
                messages=working_messages,
                model=self.config.model,
                temperature=self.config.temperature,
                base_url=self.config.base_url,
                api_key=self.config.api_key,
            )
            working_messages.append({"role": "assistant", "content": raw})

            parsed = self._parse_json(raw)
            if parsed is None:
                working_messages.append(
                    {
                        "role": "user",
                        "content": "Your output was not valid JSON. Output ONLY a single JSON object matching the schema.",
                    }
                )
                continue

            thought = str(parsed.get("thought", "")).strip()
            action = str(parsed.get("action", "")).strip()
            args = parsed.get("args", {})
            final = bool(parsed.get("final", False))
            if not isinstance(args, dict):
                args = {}

            steps.append(AgentStep(iteration=i + 1, thought=thought, action=action, args=args))

            if action == "finish":
                result = ""
                if isinstance(parsed.get("args"), dict):
                    result = str(parsed["args"].get("result", ""))
                if final is not True:
                    result = result or "finish called but final was not true"
                messages.extend(working_messages[1:])
                return result, steps

            tool_text = self._dispatch_tool(action, args)
            working_messages.append({"role": "user", "content": f"TOOL_RESULT ({action}):\n{tool_text}"})

        messages.extend(working_messages[1:])
        return "max iterations reached without finish", steps

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        if text is None:
            return None
        t = text.strip()
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            return None

    def _dispatch_tool(self, action: str, args: dict[str, Any]) -> str:
        root = self.config.workspace_root
        try:
            if action == "read_file":
                return read_file(str(args.get("path", "")), root=root)
            if action == "write_file":
                before = ""
                rel_path = str(args.get("path", ""))
                if self.config.memory_store is not None:
                    try:
                        p = _resolve_in_workspace(root, rel_path)
                        if p.exists() and p.is_file():
                            before = p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        before = ""
                result = write_file(
                    rel_path,
                    str(args.get("content", "")),
                    root=root,
                    require_confirm=True,
                    yes=self.config.yes,
                )
                if self.config.memory_store is not None:
                    try:
                        self.config.memory_store.record_file_change(
                            path=rel_path,
                            before=before,
                            after=str(args.get("content", "")),
                        )
                    except Exception:
                        pass
                return result
            if action == "list_files":
                path = str(args.get("path", "."))
                max_entries = int(args.get("max_entries", 200))
                return "\n".join(list_files(path, root=root, max_entries=max_entries))
            if action == "run_shell":
                res = run_shell(str(args.get("command", "")), root=root, confirm=self.config.yes)
                return res.to_text()
            return f"error: unknown action '{action}'"
        except ToolError as e:
            if "write requires confirmation" in str(e):
                return (
                    "error: write requires confirmation. "
                    "Ask the user to re-run with --yes or confirm in interactive mode."
                )
            if "command requires confirmation" in str(e):
                return (
                    "error: command requires confirmation. "
                    "Ask the user to re-run with --yes or enable /apply in interactive mode."
                )
            return f"error: {e}"
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"
