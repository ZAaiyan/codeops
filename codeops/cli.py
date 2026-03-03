from __future__ import annotations

import getpass
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.panel import Panel

from codeops.agent import Agent, AgentConfig
from codeops.config import global_config_path, load_global_config, load_merged_config, save_global_config
from codeops.memory import Memory
from codeops.tools import ToolError, list_files, run_shell


app = typer.Typer(add_completion=False, invoke_without_command=True)
console = Console()


auth_app = typer.Typer(add_completion=False)
app.add_typer(auth_app, name="auth")


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


@auth_app.command("login")
def auth_login(
    base_url: str = typer.Option(None, "--base-url"),
    model: str = typer.Option(None, "--model"),
) -> None:
    api_key = getpass.getpass("ARK_API_KEY / OPENAI_API_KEY: ").strip()
    if api_key == "":
        console.print("[red]ERROR[/red] empty api key")
        raise typer.Exit(code=1)

    cfg = load_global_config()
    cfg["api_key"] = api_key
    if base_url is not None and base_url.strip() != "":
        cfg["base_url"] = base_url.strip()
    if model is not None and model.strip() != "":
        cfg["model"] = model.strip()
    path = save_global_config(cfg)
    console.print(f"ok: saved to {path.as_posix()}")


@auth_app.command("path")
def auth_path() -> None:
    console.print(global_config_path().as_posix())


def _project_summary(workspace: Path) -> str:
    parts: list[str] = []
    parts.append(f"workspace: {workspace.as_posix()}")
    try:
        files = list_files(".", root=workspace, max_entries=120)
        parts.append("files:")
        parts.extend([f"- {p}" for p in files[:120]])
    except Exception:
        pass

    for name in [".gitignore", "package.json", "requirements.txt"]:
        p = workspace / name
        if p.exists() and p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if len(text) > 20_000:
                text = text[:20_000]
            parts.append(f"\n{name}:\n{text}")

    return "\n".join(parts)


def _agent_from_workspace(
    *,
    workspace: Path,
    model: str | None,
    max_iterations: int | None,
    temperature: float | None,
    yes: bool,
    debug: bool,
) -> tuple[Agent, str, int, float]:
    cfg = load_merged_config(workspace=workspace)

    model_final = (model or str(cfg.get("model") or "gpt-4o-mini")).strip()
    max_iter_final = int(max_iterations or int(cfg.get("max_iterations") or 12))
    temperature_final = float(temperature if temperature is not None else float(cfg.get("temperature") or 0.0))
    base_url = cfg.get("base_url")
    base_url_final = str(base_url).strip() if isinstance(base_url, str) and str(base_url).strip() else None
    api_key = cfg.get("api_key")
    api_key_final = str(api_key).strip() if isinstance(api_key, str) and str(api_key).strip() else None

    agent = Agent(
        config=AgentConfig(
            model=model_final,
            temperature=temperature_final,
            max_iterations=max_iter_final,
            base_url=base_url_final,
            api_key=api_key_final,
            workspace_root=workspace,
            yes=yes,
            debug=debug,
        )
    )
    return agent, model_final, max_iter_final, temperature_final


def _print_debug(*, debug: bool, data: Any) -> None:
    if not debug:
        return
    try:
        console.print(Panel.fit(json.dumps(data, ensure_ascii=False, indent=2), title="debug"))
    except Exception:
        console.print(Panel.fit(str(data), title="debug"))


def _confirm_write(session_yes: bool) -> bool:
    if session_yes:
        return True
    answer = console.input("确认写入文件？[y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _print_error(*, debug: bool, e: Exception) -> None:
    console.print(f"[red]ERROR[/red] {type(e).__name__}: {e}")
    if debug:
        console.print(Panel.fit(traceback.format_exc(), title="traceback"))


@app.callback()
def main(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes"),
    debug: bool = typer.Option(False, "--debug"),
    model: str = typer.Option(None, "--model"),
    max_iterations: int = typer.Option(None, "--max-iterations"),
    temperature: float = typer.Option(None, "--temperature"),
) -> None:
    _ensure_utf8_stdio()
    if ctx.invoked_subcommand is not None:
        return
    chat(model=model, max_iterations=max_iterations, temperature=temperature, yes=yes, debug=debug)


@app.command()
def run(
    task: str = typer.Argument(...),
    model: str = typer.Option(None, "--model"),
    max_iterations: int = typer.Option(None, "--max-iterations"),
    temperature: float = typer.Option(None, "--temperature"),
    yes: bool = typer.Option(False, "--yes"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    workspace = Path.cwd().resolve()
    agent, model_final, max_iter_final, temperature_final = _agent_from_workspace(
        workspace=workspace,
        model=model,
        max_iterations=max_iterations,
        temperature=temperature,
        yes=yes,
        debug=debug,
    )

    mem = Memory.new()
    mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))

    try:
        result, steps = agent.run(user_task=task, messages=mem.messages)
    except Exception as e:
        _print_error(debug=debug, e=e)
        raise typer.Exit(code=1)
    _print_debug(debug=debug, data={"model": model_final, "max_iterations": max_iter_final, "temperature": temperature_final})
    for step in steps:
        console.print(f"[cyan]#{step.iteration}[/cyan] action={step.action} thought={step.thought}")
    console.print("\n[bold]RESULT[/bold]")
    console.print(result)


@app.command()
def explain(
    path: str = typer.Argument(...),
    model: str = typer.Option(None, "--model"),
    max_iterations: int = typer.Option(None, "--max-iterations"),
    temperature: float = typer.Option(None, "--temperature"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    workspace = Path.cwd().resolve()
    agent, _, _, _ = _agent_from_workspace(
        workspace=workspace,
        model=model,
        max_iterations=max_iterations,
        temperature=temperature,
        yes=False,
        debug=debug,
    )
    mem = Memory.new()
    mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))
    task = f"Explain the file {path}. Use read_file tool to view it first."
    try:
        result, _ = agent.run(user_task=task, messages=mem.messages)
    except Exception as e:
        _print_error(debug=debug, e=e)
        raise typer.Exit(code=1)
    console.print(result)


@app.command()
def refactor(
    path: str = typer.Argument(...),
    model: str = typer.Option(None, "--model"),
    max_iterations: int = typer.Option(None, "--max-iterations"),
    temperature: float = typer.Option(None, "--temperature"),
    yes: bool = typer.Option(False, "--yes"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    workspace = Path.cwd().resolve()
    agent, _, _, _ = _agent_from_workspace(
        workspace=workspace,
        model=model,
        max_iterations=max_iterations,
        temperature=temperature,
        yes=yes,
        debug=debug,
    )
    mem = Memory.new()
    mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))
    task = (
        f"Refactor the project path {path}. "
        "First list_files to understand structure, then make safe improvements. "
        "Use write_file only when necessary."
    )
    try:
        result, steps = agent.run(user_task=task, messages=mem.messages)
    except Exception as e:
        _print_error(debug=debug, e=e)
        raise typer.Exit(code=1)
    for step in steps:
        console.print(f"[cyan]#{step.iteration}[/cyan] action={step.action} thought={step.thought}")
    console.print("\n[bold]RESULT[/bold]")
    console.print(result)


@app.command()
def chat(
    model: str = typer.Option(None, "--model"),
    max_iterations: int = typer.Option(None, "--max-iterations"),
    temperature: float = typer.Option(None, "--temperature"),
    yes: bool = typer.Option(False, "--yes"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    workspace = Path.cwd().resolve()
    agent, model_final, max_iter_final, temperature_final = _agent_from_workspace(
        workspace=workspace,
        model=model,
        max_iterations=max_iterations,
        temperature=temperature,
        yes=yes,
        debug=debug,
    )

    history = InMemoryHistory()
    session = PromptSession(history=history)
    mem = Memory.new()
    mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))

    session_yes = yes

    console.print(Panel.fit("进入 CodeOps 交互模式：/exit /clear /files /run <cmd> /apply", title="CodeOps"))
    _print_debug(
        debug=debug,
        data={"model": model_final, "max_iterations": max_iter_final, "temperature": temperature_final, "workspace": workspace.as_posix()},
    )

    while True:
        try:
            text = session.prompt("CodeOps > ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if text == "":
            continue

        if text == "/exit":
            break
        if text == "/clear":
            mem.clear()
            mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))
            console.print("已清空历史。")
            continue
        if text == "/files":
            try:
                files = list_files(".", root=workspace, max_entries=200)
                console.print("\n".join(files))
            except ToolError as e:
                console.print(f"[red]ERROR[/red] {e}")
            continue
        if text.startswith("/run "):
            cmd = text[len("/run ") :].strip()
            try:
                res = run_shell(cmd, root=workspace)
                console.print(res.to_text())
            except ToolError as e:
                console.print(f"[red]ERROR[/red] {e}")
            continue
        if text == "/apply":
            session_yes = True
            console.print("已开启自动写入（相当于 --yes）。")
            continue

        mem.add("user", text)

        try:
            result, steps = agent.run(user_task=text, messages=mem.messages)
        except Exception as e:
            _print_error(debug=debug, e=e)
            continue

        needs_confirm = any(s.action == "write_file" for s in steps) and not yes and not session_yes
        if needs_confirm:
            if _confirm_write(session_yes):
                agent_yes = Agent(config=AgentConfig(**{**agent.config.__dict__, "yes": True}))
                try:
                    result, steps = agent_yes.run(user_task=text, messages=mem.messages)
                except Exception as e:
                    _print_error(debug=debug, e=e)
                    continue
            else:
                console.print("[yellow]已取消写入。[/yellow]")

        _print_debug(debug=debug, data=[s.__dict__ for s in steps])
        console.print(result)
        mem.add("assistant", result)
