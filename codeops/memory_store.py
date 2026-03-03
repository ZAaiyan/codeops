from __future__ import annotations

import json
import time
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any


def _state_root_dir() -> Path:
    xdg = (Path.home() / ".config").resolve()
    try:
        import os

        x = os.getenv("XDG_CONFIG_HOME")
        if x and x.strip():
            xdg = Path(x).expanduser().resolve()
    except Exception:
        pass
    return xdg / "codeops" / "state"


def _workspace_id(workspace: Path) -> str:
    return sha1(workspace.as_posix().encode("utf-8")).hexdigest()[:12]


def _resolve_in_workspace(workspace: Path, rel_path: str) -> Path:
    p = Path(rel_path)
    if p.is_absolute():
        raise ValueError("path must be relative")
    abs_p = (workspace / p).resolve()
    if not abs_p.is_relative_to(workspace):
        raise ValueError("path escapes workspace")
    return abs_p


@dataclass
class FileChange:
    path: str
    before: str
    after: str
    ts: float

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "before": self.before, "after": self.after, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FileChange":
        return cls(
            path=str(d.get("path", "")),
            before=str(d.get("before", "")),
            after=str(d.get("after", "")),
            ts=float(d.get("ts", 0.0) or 0.0),
        )


class MemoryStore:
    def __init__(self, *, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.dir = (_state_root_dir() / _workspace_id(self.workspace)).resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self.dir / "state.json"
        self._undo: list[FileChange] = []
        self._redo: list[FileChange] = []
        self._chat: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        undo_raw = data.get("undo") or []
        redo_raw = data.get("redo") or []
        chat_raw = data.get("chat") or []
        if isinstance(undo_raw, list):
            self._undo = [FileChange.from_dict(x) for x in undo_raw if isinstance(x, dict)]
        if isinstance(redo_raw, list):
            self._redo = [FileChange.from_dict(x) for x in redo_raw if isinstance(x, dict)]
        if isinstance(chat_raw, list):
            self._chat = [x for x in chat_raw if isinstance(x, dict)]

    def _save(self) -> None:
        data = {
            "undo": [c.to_dict() for c in self._undo[-200:]],
            "redo": [c.to_dict() for c in self._redo[-200:]],
            "chat": self._chat[-200:],
        }
        self._state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_chat(self, *, role: str, content: str) -> None:
        item = {"ts": time.time(), "role": role, "content": content}
        self._chat.append(item)
        self._save()

    def record_file_change(self, *, path: str, before: str, after: str) -> None:
        rel = path.replace("\\", "/").lstrip("/")
        if rel == "":
            return
        ch = FileChange(path=rel, before=before, after=after, ts=time.time())
        self._undo.append(ch)
        self._redo.clear()
        self._save()

    def undo(self) -> str:
        if not self._undo:
            return "nothing to undo"
        ch = self._undo.pop()
        p = _resolve_in_workspace(self.workspace, ch.path)
        current = ""
        try:
            current = p.read_text(encoding="utf-8")
        except Exception:
            current = ""
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(ch.before, encoding="utf-8")
        self._redo.append(FileChange(path=ch.path, before=current, after=ch.before, ts=time.time()))
        self._save()
        return f"ok: undo {ch.path}"

    def redo(self) -> str:
        if not self._redo:
            return "nothing to redo"
        ch = self._redo.pop()
        p = _resolve_in_workspace(self.workspace, ch.path)
        current = ""
        try:
            current = p.read_text(encoding="utf-8")
        except Exception:
            current = ""
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(ch.after, encoding="utf-8")
        self._undo.append(FileChange(path=ch.path, before=current, after=ch.after, ts=time.time()))
        self._save()
        return f"ok: redo {ch.path}"

    def context_snippet(self, *, max_chars: int = 4000) -> str:
        parts: list[str] = []
        if self._chat:
            parts.append("chat:")
            for item in self._chat[-12:]:
                role = str(item.get("role", ""))
                content = str(item.get("content", ""))
                parts.append(f"- {role}: {content}")
        if self._undo:
            parts.append("recent file changes:")
            for ch in self._undo[-8:]:
                parts.append(f"- {ch.path}")
        text = "\n".join(parts)
        if len(text) > max_chars:
            return text[-max_chars:]
        return text
