from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Memory:
    messages: list[dict[str, Any]]
    max_chars: int = 60_000

    @classmethod
    def new(cls) -> "Memory":
        return cls(messages=[])

    def clear(self) -> None:
        self.messages.clear()

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def trim(self) -> None:
        if self.max_chars <= 0:
            return
        total = 0
        kept: list[dict[str, Any]] = []
        for msg in reversed(self.messages):
            content = str(msg.get("content", ""))
            total += len(content)
            kept.append(msg)
            if total >= self.max_chars:
                break
        self.messages = list(reversed(kept))
