from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Memory:
    messages: list[dict[str, Any]]

    @classmethod
    def new(cls) -> "Memory":
        return cls(messages=[])

    def clear(self) -> None:
        self.messages.clear()

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
