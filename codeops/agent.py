from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codeops.llm import chat_completion
from codeops.tools import ToolError, list_files, read_file, run_shell, write_file


@dataclass(frozen=True)
class AgentConfig:
    model: str
    temperature: float = 0.0
    max_iterations: int = 12
    base_url: str | None = None
    api_key: str | None = None
    workspace_root: Path | None = None
    yes: bool = False
    debug: bool = False


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


class Agent:
    def __init__(self, *, config: AgentConfig) -> None:
        self.config = config

    def run(self, *, user_task: str, messages: list[dict[str, Any]]) -> tuple[str, list[AgentStep]]:
        working_messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
        working_messages.extend(messages)
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
                return write_file(
                    str(args.get("path", "")),
                    str(args.get("content", "")),
                    root=root,
                    require_confirm=True,
                    yes=self.config.yes,
                )
            if action == "list_files":
                path = str(args.get("path", "."))
                max_entries = int(args.get("max_entries", 200))
                return "\n".join(list_files(path, root=root, max_entries=max_entries))
            if action == "run_shell":
                res = run_shell(str(args.get("command", "")), root=root)
                return res.to_text()
            return f"error: unknown action '{action}'"
        except ToolError as e:
            if "write requires confirmation" in str(e):
                return (
                    "error: write requires confirmation. "
                    "Ask the user to re-run with --yes or confirm in interactive mode."
                )
            return f"error: {e}"
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"
