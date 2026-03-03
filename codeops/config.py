from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def global_config_path() -> Path:
    xdg = os.getenv("XDG_CONFIG_HOME")
    if xdg and xdg.strip():
        base = Path(xdg).expanduser()
    else:
        base = Path.home() / ".config"
    return (base / "codeops" / "config.yaml").resolve()


def _parse_scalar(text: str) -> Any:
    t = text.strip()
    if t == "":
        return ""
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        return t[1:-1]
    lower = t.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        return int(t)
    except Exception:
        pass
    try:
        return float(t)
    except Exception:
        pass
    return t


def _load_yamlish(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        k = key.strip()
        if not k:
            continue
        out[k] = _parse_scalar(val)
    return out


def _dump_yamlish(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for k, v in data.items():
        key = str(k).strip()
        if key == "":
            continue
        if isinstance(v, bool):
            val = "true" if v else "false"
        elif isinstance(v, int):
            val = str(v)
        elif isinstance(v, float):
            val = str(v)
        else:
            s = "" if v is None else str(v)
            needs_quote = s == "" or any(ch in s for ch in ("#", ":", " "))
            if needs_quote:
                esc = s.replace("\\", "\\\\").replace('"', '\\"')
                val = f"\"{esc}\""
            else:
                val = s
        lines.append(f"{key}: {val}")
    return "\n".join(lines) + ("\n" if lines else "")


def load_merged_config(*, workspace: Path) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    merged.update(_load_yamlish(global_config_path()))
    merged.update(_load_yamlish(workspace / ".codeops.yaml"))
    return merged


def load_global_config() -> dict[str, Any]:
    return _load_yamlish(global_config_path())


def save_global_config(data: dict[str, Any]) -> Path:
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_yamlish(data), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path
