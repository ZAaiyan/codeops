from __future__ import annotations

import getpass
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from rich.console import Console
from rich.panel import Panel

from codeops.agent import Agent, AgentConfig
from codeops.config import global_config_path, load_global_config, load_merged_config, save_global_config
from codeops.memory import Memory
from codeops.memory_store import MemoryStore
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


class _SlashCommandCompleter(Completer):
    def __init__(self) -> None:
        self._cmds = [
            "/apply",
            "/clear",
            "/exit",
            "/files",
            "/help",
            "/plan",
            "/redo",
            "/run ",
            "/undo",
        ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for c in self._cmds:
            if c.startswith(text):
                yield Completion(c, start_position=-len(text))


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
        top: dict[str, int] = {}
        for fp in files:
            head = fp.split("/", 1)[0]
            top[head] = top.get(head, 0) + 1
        if top:
            parts.append("repo_outline:")
            for k in sorted(top.keys()):
                parts.append(f"- {k}: {top[k]} files (sampled)")
        parts.append("files:")
        parts.extend([f"- {p}" for p in files[:120]])
    except Exception:
        pass

    deps: list[str] = []
    req = workspace / "requirements.txt"
    if req.exists() and req.is_file():
        try:
            for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or s.startswith("-"):
                    continue
                deps.append(s)
        except Exception:
            pass

    pyproject = workspace / "pyproject.toml"
    if pyproject.exists() and pyproject.is_file():
        try:
            txt = pyproject.read_text(encoding="utf-8", errors="replace")
            in_deps = False
            for raw in txt.splitlines():
                line = raw.strip()
                if line.startswith("[") and line.endswith("]"):
                    in_deps = False
                if line == "dependencies = [":
                    in_deps = True
                    continue
                if in_deps:
                    if line.startswith("]"):
                        in_deps = False
                        continue
                    s = line.strip().strip(",").strip().strip('"').strip("'")
                    if s:
                        deps.append(s)
        except Exception:
            pass

    if deps:
        parts.append("dependencies:")
        for d in deps[:60]:
            parts.append(f"- {d}")

    for name in [".gitignore", "package.json", "requirements.txt", "pyproject.toml"]:
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
    planner_model = str(cfg.get("planner_model") or "").strip() or None
    max_iter_final = int(max_iterations or int(cfg.get("max_iterations") or 12))
    temperature_final = float(temperature if temperature is not None else float(cfg.get("temperature") or 0.0))
    max_context_chars = int(cfg.get("max_context_chars") or 60_000)
    base_url = cfg.get("base_url")
    base_url_final = str(base_url).strip() if isinstance(base_url, str) and str(base_url).strip() else None
    api_key = cfg.get("api_key")
    api_key_final = str(api_key).strip() if isinstance(api_key, str) and str(api_key).strip() else None

    agent = Agent(
        config=AgentConfig(
            model=model_final,
            temperature=temperature_final,
            max_iterations=max_iter_final,
            planner_model=planner_model,
            base_url=base_url_final,
            api_key=api_key_final,
            workspace_root=workspace,
            yes=yes,
            debug=debug,
            max_context_chars=max_context_chars,
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
    store = MemoryStore(workspace=workspace)
    agent = Agent(config=AgentConfig(**{**agent.config.__dict__, "memory_store": store}))

    mem = Memory.new()
    mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))
    snippet = store.context_snippet()
    if snippet.strip():
        mem.add("user", "LONG_TERM_MEMORY:\n" + snippet)

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
    store = MemoryStore(workspace=workspace)
    agent = Agent(config=AgentConfig(**{**agent.config.__dict__, "memory_store": store}))
    mem = Memory.new()
    mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))
    snippet = store.context_snippet()
    if snippet.strip():
        mem.add("user", "LONG_TERM_MEMORY:\n" + snippet)
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
    store = MemoryStore(workspace=workspace)
    agent = Agent(config=AgentConfig(**{**agent.config.__dict__, "memory_store": store}))
    mem = Memory.new()
    mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))
    snippet = store.context_snippet()
    if snippet.strip():
        mem.add("user", "LONG_TERM_MEMORY:\n" + snippet)
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
    agent_base, model_final, max_iter_final, temperature_final = _agent_from_workspace(
        workspace=workspace,
        model=model,
        max_iterations=max_iterations,
        temperature=temperature,
        yes=yes,
        debug=debug,
    )
    store = MemoryStore(workspace=workspace)
    agent_base = Agent(config=AgentConfig(**{**agent_base.config.__dict__, "memory_store": store}))

    history = InMemoryHistory()
    session = PromptSession(history=history, completer=_SlashCommandCompleter(), auto_suggest=AutoSuggestFromHistory())
    mem = Memory.new()
    mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))
    snippet = store.context_snippet()
    if snippet.strip():
        mem.add("user", "LONG_TERM_MEMORY:\n" + snippet)

    session_yes = yes
    plan_enabled = True
    last_plan: list[str] = []

    console.print(Panel.fit("进入 CodeOps 交互模式：/help 查看指令", title="CodeOps"))
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
        if text == "/help":
            console.print(
                "\n".join(
                    [
                        "/exit 退出",
                        "/clear 清空历史",
                        "/files 查看当前目录文件",
                        "/run <cmd> 执行安全受限的 shell 命令",
                        "/plan 切换/查看计划",
                        "/undo 撤销最近一次写入",
                        "/redo 重做最近一次撤销",
                        "/apply 开启自动写入/危险命令确认（相当于 --yes）",
                    ]
                )
            )
            continue
        if text == "/clear":
            mem.clear()
            mem.add("user", "PROJECT_CONTEXT:\n" + _project_summary(workspace))
            snippet = store.context_snippet()
            if snippet.strip():
                mem.add("user", "LONG_TERM_MEMORY:\n" + snippet)
            console.print("已清空历史。")
            continue
        if text == "/files":
            try:
                files = list_files(".", root=workspace, max_entries=200)
                console.print("\n".join(files))
            except ToolError as e:
                console.print(f"[red]ERROR[/red] {e}")
            continue
        if text == "/undo":
            console.print(store.undo())
            continue
        if text == "/redo":
            console.print(store.redo())
            continue
        if text == "/plan":
            plan_enabled = not plan_enabled
            state = "开启" if plan_enabled else "关闭"
            console.print(f"任务规划：{state}")
            if last_plan:
                console.print(Panel.fit("\n".join([f"{i+1}. {s}" for i, s in enumerate(last_plan)]), title="last plan"))
            continue
        if text.startswith("/run "):
            cmd = text[len("/run ") :].strip()
            try:
                res = run_shell(cmd, root=workspace, confirm=session_yes or yes)
                console.print(res.to_text())
            except ToolError as e:
                console.print(f"[red]ERROR[/red] {e}")
            continue
        if text == "/apply":
            session_yes = True
            console.print("已开启自动写入（相当于 --yes）。")
            continue

        mem.add("user", text)
        store.add_chat(role="user", content=text)
        mem.trim()

        agent = Agent(config=AgentConfig(**{**agent_base.config.__dict__, "yes": bool(session_yes or yes)}))

        if plan_enabled:
            plan = agent.plan(user_task=text, messages=mem.messages)
            last_plan = plan
        else:
            plan = []

        if plan:
            console.print(Panel.fit("\n".join([f"{i+1}. {s}" for i, s in enumerate(plan)]), title="plan"))
            mem.add("user", "PLAN:\n" + "\n".join([f"- {s}" for s in plan]))
            store.add_chat(role="user", content="PLAN:\n" + "\n".join(plan))
            mem.trim()
            for idx, step_text in enumerate(plan, start=1):
                step_task = f"[{idx}/{len(plan)}] {step_text}"
                try:
                    result, steps = agent.run(user_task=step_task, messages=mem.messages)
                except Exception as e:
                    _print_error(debug=debug, e=e)
                    break
                needs_confirm = any(s.action == "write_file" for s in steps) and not (session_yes or yes)
                if needs_confirm:
                    if _confirm_write(session_yes):
                        agent_yes = Agent(config=AgentConfig(**{**agent.config.__dict__, "yes": True}))
                        try:
                            result, steps = agent_yes.run(user_task=step_task, messages=mem.messages)
                        except Exception as e:
                            _print_error(debug=debug, e=e)
                            break
                    else:
                        console.print("[yellow]已取消写入。[/yellow]")

                _print_debug(debug=debug, data=[s.__dict__ for s in steps])
                console.print(result)
                mem.add("assistant", result)
                store.add_chat(role="assistant", content=result)
                mem.trim()
            else:
                pass
        else:
            try:
                result, steps = agent.run(user_task=text, messages=mem.messages)
            except Exception as e:
                _print_error(debug=debug, e=e)
                continue

            needs_confirm = any(s.action == "write_file" for s in steps) and not (session_yes or yes)
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
            store.add_chat(role="assistant", content=result)
            mem.trim()
