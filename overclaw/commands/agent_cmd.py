"""OverClaw agent management commands.

overclaw agent register <name> <module:function>
overclaw agent list
overclaw agent remove <name>
overclaw agent update <name> <module:function>
overclaw agent show <name>
overclaw agent validate <name> --data <path>
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

from overclaw.utils.display import BRAND, confirm_option, rel, select_option
from overclaw.core.paths import (
    agent_experiments_dir,
    agent_setup_spec_dir,
    load_agent_dotenv,
    load_overclaw_dotenv,
)
from overclaw.core.constants import overclaw_rel
from overclaw.core.registry import (
    EntrypointNotFoundError,
    EntrypointSignatureError,
    load_registry,
    remove_agent,
    resolve_entrypoint,
    resolve_entrypoint_file,
    resolve_module_to_file,
    save_agent,
)
from overclaw.commands.agent_env import (
    collect_agent_provider_config,
    instrument_agent_files,
)


def _other_agents_with_entrypoint(
    registry: dict[str, dict[str, str]],
    entrypoint: str,
    *,
    exclude_name: str | None = None,
) -> list[str]:
    """Return registered agent names (other than *exclude_name*) using this entrypoint."""
    ep = entrypoint.strip()
    return sorted(
        n
        for n, data in registry.items()
        if (exclude_name is None or n != exclude_name)
        and data.get("entrypoint", "").strip() == ep
    )


def _confirm_duplicate_entrypoint(
    console: Console,
    entrypoint: str,
    existing_names: list[str],
    *,
    for_update: bool = False,
) -> None:
    """Print a warning and exit unless the user confirms."""
    listed = ", ".join(f"[bold]{n}[/bold]" for n in existing_names)
    console.print(
        f"\n  [yellow]Warning:[/yellow] This entrypoint is already registered for: {listed}\n"
        f"  [dim]{entrypoint}[/dim]\n\n"
        f"  Each agent name has its own tree under [bold]{overclaw_rel('agents')}/[/bold] "
        "(setup_spec, experiments). The same entrypoint can be registered twice with "
        "different names; eval data stays separate per name.\n"
    )
    prompt = (
        "Point this agent at that shared entrypoint anyway?"
        if for_update
        else "Register this entrypoint for another agent name anyway?"
    )
    if not confirm_option(prompt, default=False, console=console):
        console.print("  [dim]Aborted.[/dim]\n")
        raise SystemExit(0)


def _ensure_model_for_wrapper(console: Console) -> None:
    """Make sure an LLM model is available for wrapper generation.

    Assumes overclaw dotenv is already loaded.  If no model is configured,
    prompts the user to select one for this session.
    """
    import os
    from overclaw.utils.model_picker import prompt_for_catalog_litellm_model
    from overclaw.utils.models import DEFAULT_ANALYZER_MODEL

    for env_var in ("ANALYZER_MODEL", "ENV_SETUP_MODEL"):
        if os.environ.get(env_var, "").strip():
            return

    try:
        import litellm  # noqa: F401

        return
    except ImportError:
        pass

    console.print(
        "\n  [dim]Wrapper generation requires an LLM model. "
        "Select one to continue.[/dim]"
    )
    model = prompt_for_catalog_litellm_model(
        console,
        select_prompt="  Select model for wrapper generation (number)",
        default_model=DEFAULT_ANALYZER_MODEL,
        no_catalog_prompt="  Enter model identifier",
    )
    os.environ["ANALYZER_MODEL"] = model


def _offer_wrapper_generation(
    name: str,
    exc: EntrypointNotFoundError | EntrypointSignatureError,
    agent_path: str,
    console: Console,
) -> tuple[str, Path, str] | None:
    """Prompt the user to auto-generate an entrypoint wrapper.

    The wrapper is generated inside ``.overclaw/agents/<name>/`` — the
    user's original code is never modified.

    Returns ``(new_entrypoint, file_path, fn_name)`` on success, or
    ``None`` if the user declines or generation fails.
    """
    from overclaw.entrypoint_wrapper import (
        generate_entrypoint_wrapper,
        wrapper_entrypoint,
    )
    from overclaw.utils.display import make_spinner_progress

    agent_dir = Path(agent_path).resolve().parent

    if isinstance(exc, EntrypointSignatureError):
        console.print(
            f"\n  [bold yellow]\u26a0[/bold yellow]  [bold]{exc.fn_name}()[/bold] in "
            f"[cyan]{rel(exc.file_path)}[/cyan] {exc.reason}.\n"
        )
    else:
        console.print(
            f"\n  [bold yellow]\u26a0[/bold yellow]  [bold]{exc.fn_name}()[/bold] is not a callable "
            f"function in [cyan]{rel(exc.file_path)}[/cyan].\n"
        )
    console.print(
        "  OverClaw needs a function that takes input and returns output, e.g.:\n"
        "    [dim]def run(input_data: dict) -> dict[/dim]\n"
    )

    choice = select_option(
        [
            "Generate an entrypoint wrapper (OverClaw reads your code and creates one)",
            "I'll write the wrapper myself (exit, re-run when ready)",
            "Point me to a different module:function (exit)",
        ],
        title="How would you like to proceed?",
        default_index=0,
        console=console,
    )

    if choice != 0:
        if choice == 1:
            console.print(
                f"\n  Create a wrapper with [bold]def run(input_data: dict) -> dict[/bold] "
                f"and re-register:\n"
                f"    [bold]overclaw agent register {name} <module:run>[/bold]\n"
            )
        else:
            console.print(
                f"\n  Re-register with the correct entrypoint:\n"
                f"    [bold]overclaw agent register {name} <module:function>[/bold]\n"
            )
        return None

    _ensure_model_for_wrapper(console)

    console.print()
    with make_spinner_progress(console) as progress:
        progress.add_task("  Analyzing agent code and generating wrapper\u2026")
        wp = generate_entrypoint_wrapper(agent_dir, name)

    if wp == "refused":
        console.print(
            "\n  [bold yellow]⚠[/bold yellow]  This agent's code is too complex for an "
            "auto-generated wrapper.\n\n"
            "  The wrapper needs to be a trivial bridge (import + call), but this\n"
            "  agent would require re-implementing agent-specific logic.\n\n"
            "  Add a [bold]def run(input_data: dict) -> dict[/bold] function directly\n"
            "  in your agent code, then re-register:\n"
            f"    [bold]overclaw agent register {name} <your_module:run>[/bold]\n"
        )
        return None

    if wp is None or not wp.is_file():
        console.print(
            "\n  [bold red]✗[/bold red]  Wrapper generation failed.\n"
            "  This can happen if no LLM model is configured.\n"
            f"  Set [bold]ANALYZER_MODEL[/bold] in [bold]{overclaw_rel('.env')}[/bold] "
            "or write the wrapper manually.\n"
        )
        return None

    wrapper_code = wp.read_text(encoding="utf-8")

    console.print()
    console.print(
        Panel(
            f"[bold green]Generated entrypoint wrapper[/bold green]\n\n"
            f"  File:     [cyan]{rel(wp)}[/cyan]\n"
            f"  Function: [bold]run(input_data: dict) -> dict[/bold]",
            border_style="green",
            padding=(1, 2),
        )
    )

    if confirm_option("Review the generated code?", default=True, console=console):
        console.print()
        console.print(
            Syntax(
                wrapper_code,
                "python",
                theme="monokai",
                line_numbers=True,
                word_wrap=True,
            )
        )

    console.print()
    if not confirm_option(
        "Register with this entrypoint?", default=True, console=console
    ):
        console.print(
            f"\n  [dim]Edit [cyan]{rel(wp)}[/cyan] and re-run register.[/dim]\n"
        )
        return None

    new_ep = wrapper_entrypoint(name)
    return new_ep, wp, "run"


def _auto_generate_wrapper(
    name: str,
    agent_path: str,
    console: Console,
) -> tuple[str, Path, str] | None:
    """Generate an entrypoint wrapper when the user provided only a filename.

    Unlike :func:`_offer_wrapper_generation`, this path is taken *before* any
    entrypoint validation — the user has already confirmed they want auto-gen,
    so we skip the "function not found" preamble and go straight to generation.

    Returns ``(new_entrypoint, file_path, fn_name)`` on success, or ``None``
    if the user declines or generation fails.
    """
    from overclaw.entrypoint_wrapper import (
        generate_entrypoint_wrapper,
        wrapper_entrypoint,
    )
    from overclaw.utils.display import make_spinner_progress

    agent_dir = Path(agent_path).resolve().parent

    _ensure_model_for_wrapper(console)

    console.print()
    with make_spinner_progress(console) as progress:
        progress.add_task("  Analyzing agent code and generating wrapper…")
        wp = generate_entrypoint_wrapper(agent_dir, name)

    if wp == "refused":
        console.print(
            "\n  [bold yellow]⚠[/bold yellow]  This agent's code is too complex for an "
            "auto-generated wrapper.\n\n"
            "  The wrapper needs to be a trivial bridge (import + call), but this\n"
            "  agent would require re-implementing agent-specific logic.\n\n"
            "  Add a [bold]def run(input_data: dict) -> dict[/bold] function directly\n"
            "  in your agent code, then re-register:\n"
            f"    [bold]overclaw agent register {name} <your_module:run>[/bold]\n"
        )
        return None

    if wp is None or not wp.is_file():
        console.print(
            "\n  [bold red]✗[/bold red]  Wrapper generation failed.\n"
            "  This can happen if no LLM model is configured.\n"
            f"  Set [bold]ANALYZER_MODEL[/bold] in [bold]{overclaw_rel('.env')}[/bold] "
            "or write the wrapper manually.\n"
        )
        return None

    wrapper_code = wp.read_text(encoding="utf-8")

    console.print()
    console.print(
        Panel(
            f"[bold green]Generated entrypoint wrapper[/bold green]\n\n"
            f"  File:     [cyan]{rel(wp)}[/cyan]\n"
            f"  Function: [bold]run(input_data: dict) -> dict[/bold]",
            border_style="green",
            padding=(1, 2),
        )
    )

    if confirm_option("Review the generated code?", default=True, console=console):
        console.print()
        console.print(
            Syntax(
                wrapper_code,
                "python",
                theme="monokai",
                line_numbers=True,
                word_wrap=True,
            )
        )

    console.print()
    if not confirm_option(
        "Register with this entrypoint?", default=True, console=console
    ):
        console.print(
            f"\n  [dim]Edit [cyan]{rel(wp)}[/cyan] and re-run register.[/dim]\n"
        )
        return None

    new_ep = wrapper_entrypoint(name)
    return new_ep, wp, "run"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_register(name: str, entrypoint: str) -> None:
    console = Console()
    load_overclaw_dotenv()
    registry = load_registry()

    if name in registry:
        current_ep = registry[name]["entrypoint"].strip()
        if current_ep == entrypoint.strip():
            raise SystemExit(0)
        console.print(
            f"\n  [bold red]Error:[/bold red] "
            f"Agent '[bold]{name}[/bold]' is already registered.\n"
            f"  Current entrypoint: [dim]{registry[name]['entrypoint']}[/dim]\n\n"
            f"  To use a different entrypoint:\n"
            f"    [bold]overclaw agent update {name} <module:function>[/bold]\n"
        )
        raise SystemExit(1)

    # ---- Handle filename-only input (no entrypoint function specified) ----
    if ":" not in entrypoint:
        file_path = resolve_module_to_file(entrypoint)
        if file_path is None:
            console.print(
                f"\n  [bold red]Error:[/bold red] "
                f"Cannot find file for '[bold]{entrypoint}[/bold]'.\n\n"
                f"  If this is a module path, specify a function too:\n"
                f"    [bold]overclaw agent register {name} {entrypoint}:run[/bold]\n"
            )
            raise SystemExit(1)

        console.print(
            f"\n  [bold yellow]No entrypoint function specified.[/bold yellow]\n"
            f"  [dim]File:[/dim] [cyan]{rel(file_path)}[/cyan]\n\n"
            f"  Since no entrypoint was specified, an overclaw entrypoint wrapper\n"
            f"  will be generated automatically for this agent.\n"
        )
        if not confirm_option(
            "Generate entrypoint automatically?", default=True, console=console
        ):
            console.print(
                f"\n  [dim]Re-register with an explicit entrypoint:\n"
                f"    [bold]overclaw agent register {name} {entrypoint}:run[/bold][/dim]\n"
            )
            raise SystemExit(0)

        agent_path = str(file_path)

        console.print()
        console.print(Rule(style="dim"))
        collect_agent_provider_config(name, console)
        load_agent_dotenv(name)

        console.print()
        console.print(Rule(style="dim"))
        instrument_agent_files(agent_path, name, console)

        result = _auto_generate_wrapper(name, agent_path, console)
        if result is None:
            raise SystemExit(1)
        entrypoint, file_path, fn = result

        save_agent(name, entrypoint)
        console.print(
            f"\n  [bold green]\u2713[/bold green]  "
            f"Agent '[bold]{name}[/bold]' registered.\n"
            f"  [dim]Entrypoint:[/dim] {entrypoint}\n"
            f"  [dim]File:[/dim]      {file_path}\n"
            f"  [dim]Function:[/dim]  {fn}\n\n"
            f"  Next step: [bold {BRAND}]overclaw setup {name}[/bold {BRAND}]\n"
        )
        return

    dupes = _other_agents_with_entrypoint(registry, entrypoint)
    if dupes:
        _confirm_duplicate_entrypoint(console, entrypoint, dupes, for_update=False)

    # ---- Resolve module path to file (quick, no function check yet) ----
    try:
        file_path, fn = resolve_entrypoint_file(entrypoint)
        agent_path = str(file_path)
    except ValueError as exc:
        console.print(f"\n  [bold red]Error:[/bold red] {exc}\n")
        raise SystemExit(1) from exc

    # ---- 1. Collect agent-specific env vars (API keys) ----
    console.print()
    console.print(Rule(style="dim"))
    collect_agent_provider_config(name, console)
    load_agent_dotenv(name)

    # ---- 2. Copy agent source into .overclaw/ (instrumentation) ----
    console.print()
    console.print(Rule(style="dim"))
    instrument_agent_files(agent_path, name, console)

    # ---- 3. Validate entrypoint function (may trigger wrapper generation) ----
    try:
        resolve_entrypoint(entrypoint)
    except (EntrypointNotFoundError, EntrypointSignatureError) as exc:
        result = _offer_wrapper_generation(name, exc, agent_path, console)
        if result is None:
            raise SystemExit(1) from exc
        entrypoint = result[0]
        file_path = result[1]
        fn = result[2]

    # ---- 4. Save to registry ----
    save_agent(name, entrypoint)

    console.print(
        f"\n  [bold green]\u2713[/bold green]  "
        f"Agent '[bold]{name}[/bold]' registered.\n"
        f"  [dim]Entrypoint:[/dim] {entrypoint}\n"
        f"  [dim]File:[/dim]      {file_path}\n"
        f"  [dim]Function:[/dim]  {fn}\n\n"
        f"  Next step: [bold {BRAND}]overclaw setup {name}[/bold {BRAND}]\n"
    )


def cmd_list() -> None:
    console = Console()
    registry = load_registry()

    if not registry:
        console.print(
            "\n  [dim]No agents registered yet.[/dim]\n\n"
            "  Register one:\n"
            "    [bold]overclaw agent register <name> <module:function>[/bold]\n"
        )
        return

    table = Table(border_style="cyan", show_header=True, show_lines=False)
    table.add_column("NAME", style=f"bold {BRAND}")
    table.add_column("ENTRYPOINT")
    table.add_column("FILE", justify="center")

    for name, data in registry.items():
        file_ok = (
            "[green]\u2713[/green]"
            if Path(data["file_path"]).exists()
            else "[red]\u2717[/red]"
        )
        table.add_row(name, data["entrypoint"], file_ok)

    console.print()
    console.print(table)
    console.print()


def cmd_remove(name: str) -> None:
    console = Console()
    registry = load_registry()

    if name not in registry:
        console.print(
            f"\n  [bold red]Error:[/bold red] "
            f"Agent '[bold]{name}[/bold]' is not registered.\n\n"
            "  To see all registered agents:\n"
            "    [bold]overclaw agent list[/bold]\n"
        )
        raise SystemExit(1)

    console.print(
        f"\n  Agent '[bold]{name}[/bold]'  [dim]{registry[name]['entrypoint']}[/dim]"
    )
    if not confirm_option(
        f"Remove '{name}' from the registry?", default=True, console=console
    ):
        console.print("  [dim]Aborted.[/dim]\n")
        raise SystemExit(0)

    remove_agent(name)

    from overclaw.core.paths import agent_instrumented_dir

    inst_dir = agent_instrumented_dir(name)
    if inst_dir.exists():
        import shutil

        shutil.rmtree(inst_dir)
        console.print(f"  [dim]Removed instrumented copy at {rel(inst_dir)}[/dim]")

    console.print(
        f"\n  [bold green]\u2713[/bold green]  Agent '[bold]{name}[/bold]' removed.\n"
    )


def cmd_update(name: str, entrypoint: str) -> None:
    console = Console()
    load_overclaw_dotenv()
    registry = load_registry()

    if name not in registry:
        console.print(
            f"\n  [bold red]Error:[/bold red] "
            f"Agent '[bold]{name}[/bold]' is not registered.\n\n"
            f"  Use register instead:\n"
            f"    [bold]overclaw agent register {name} {entrypoint}[/bold]\n"
        )
        raise SystemExit(1)

    old_ep_raw = registry[name]["entrypoint"]
    if old_ep_raw.strip() == entrypoint.strip():
        raise SystemExit(0)

    dupes = _other_agents_with_entrypoint(registry, entrypoint, exclude_name=name)
    if dupes:
        _confirm_duplicate_entrypoint(console, entrypoint, dupes, for_update=True)

    try:
        file_path, _ = resolve_entrypoint_file(entrypoint)
        agent_path = str(file_path)
    except ValueError as exc:
        console.print(f"\n  [bold red]Error:[/bold red] {exc}\n")
        raise SystemExit(1) from exc

    # 1. Re-collect envs
    console.print()
    console.print(Rule(style="dim"))
    collect_agent_provider_config(name, console)
    load_agent_dotenv(name)

    # 2. Re-instrument
    console.print()
    console.print(Rule(style="dim"))
    instrument_agent_files(agent_path, name, console)

    # 3. Validate entrypoint function (may trigger wrapper generation)
    try:
        resolve_entrypoint(entrypoint)
    except (EntrypointNotFoundError, EntrypointSignatureError) as exc:
        result = _offer_wrapper_generation(name, exc, agent_path, console)
        if result is None:
            raise SystemExit(1) from exc
        entrypoint = result[0]

    # 4. Save
    save_agent(name, entrypoint)

    console.print(
        f"\n  [dim]Old entrypoint:[/dim] {old_ep_raw}\n"
        f"  [dim]New entrypoint:[/dim] {entrypoint}\n"
    )


def cmd_show(name: str) -> None:
    console = Console()
    registry = load_registry()

    if name not in registry:
        console.print(
            f"\n  [bold red]Error:[/bold red] "
            f"Agent '[bold]{name}[/bold]' is not registered.\n\n"
            "  To see all registered agents:\n"
            "    [bold]overclaw agent list[/bold]\n"
        )
        raise SystemExit(1)

    data = registry[name]
    file_path = Path(data["file_path"])
    file_exists = file_path.exists()

    spec_path = agent_setup_spec_dir(name) / "eval_spec.json"
    spec_exists = spec_path.exists()

    experiments_dir = agent_experiments_dir(name)
    exp_files = (
        [f for f in experiments_dir.rglob("*") if f.is_file() and f.name != ".gitkeep"]
        if experiments_dir.exists()
        else []
    )

    file_status = (
        "[green]\u2713 exists[/green]" if file_exists else "[red]\u2717 not found[/red]"
    )
    spec_status = (
        "[green]\u2713 ready[/green]" if spec_exists else "[yellow]not run yet[/yellow]"
    )
    exp_status = (
        f"[green]\u2713 {len(exp_files)} file(s)[/green]"
        if exp_files
        else "[yellow]not run yet[/yellow]"
    )

    lines = (
        f"[bold]Name:[/bold]        {name}\n"
        f"[bold]Entrypoint:[/bold]  {data['entrypoint']}\n"
        f"[bold]File:[/bold]        {data['file_path']}  {file_status}\n"
        f"[bold]Setup spec:[/bold]  {spec_path}  {spec_status}\n"
        f"[bold]Experiments:[/bold] {exp_status}"
    )

    console.print()
    console.print(
        Panel(
            lines,
            title=f"[bold {BRAND}]{name}[/bold {BRAND}]",
            border_style=BRAND,
            padding=(1, 2),
        )
    )
    console.print()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def cmd_validate(name: str, data: str) -> None:
    """Run the agent's entrypoint against test data to verify it works."""
    import json

    from overclaw.core.paths import agent_instrumented_dir
    from overclaw.core.registry import project_root_from_agent_file, resolve_agent
    from overclaw.optimize.data import load_data
    from overclaw.optimize.runner import AgentRunner, RunnerConfig
    from overclaw.utils.display import make_spinner_progress

    console = Console()
    load_overclaw_dotenv()

    registry = load_registry()
    if name not in registry:
        console.print(
            f"\n  [bold red]Error:[/bold red] "
            f"Agent '[bold]{name}[/bold]' is not registered.\n\n"
            "  To see all registered agents:\n"
            "    [bold]overclaw agent list[/bold]\n"
        )
        raise SystemExit(1)

    load_agent_dotenv(name)
    agent_path, fn_name = resolve_agent(name)

    data_path = Path(data)
    if not data_path.exists():
        console.print(
            f"\n  [bold red]Error:[/bold red] "
            f"Data path not found: [cyan]{data}[/cyan]\n"
        )
        raise SystemExit(1)

    json_files: list[Path] = []
    if data_path.is_dir():
        json_files = sorted(data_path.glob("*.json"))
        if not json_files:
            console.print(
                f"\n  [bold red]Error:[/bold red] "
                f"No .json files found in [cyan]{data}[/cyan]\n"
            )
            raise SystemExit(1)
    else:
        json_files = [data_path]

    cases: list[dict] = []
    for jf in json_files:
        try:
            cases.extend(load_data(str(jf)))
        except Exception as exc:
            console.print(
                f"\n  [bold red]Error:[/bold red] "
                f"Could not load [cyan]{jf}[/cyan]: {exc}\n"
            )
            raise SystemExit(1)

    if not cases:
        console.print(
            f"\n  [bold yellow]Warning:[/bold yellow] "
            f"No test cases found in [cyan]{data}[/cyan]\n"
        )
        raise SystemExit(1)

    first_case = cases[0]
    test_input = first_case.get("input", first_case)
    label = json.dumps(test_input, default=str)
    if len(label) > 120:
        label = label[:117] + "..."

    console.print(
        f"\n  Agent:      [bold]{name}[/bold]\n"
        f"  Entrypoint: [dim]{registry[name]['entrypoint']}[/dim]\n"
        f"  Data:       [cyan]{data}[/cyan]  ({len(cases)} case(s), running first)\n"
        f"  Input:      [dim]{label}[/dim]\n"
    )

    p = Path(agent_path).resolve()
    inst_root = agent_instrumented_dir(name)
    if inst_root.exists() and str(p).startswith(str(inst_root)):
        resolved_agent_dir = inst_root
        env_dir_path: Path | None = project_root_from_agent_file(agent_path) or p.parent
    else:
        pr = project_root_from_agent_file(agent_path)
        resolved_agent_dir = pr if pr is not None else p.parent
        env_dir_path = None

    entry_file = str(p.relative_to(resolved_agent_dir))
    runner = AgentRunner(
        agent_dir=resolved_agent_dir,
        entry_file=entry_file,
        entrypoint_fn=fn_name,
        config=RunnerConfig(timeout=300),
        env_dir=env_dir_path,
    )

    console.print(Rule(style="dim"))
    with make_spinner_progress(console, transient=True) as progress:
        progress.add_task("  Setting up agent environment…")
        runner.ensure_environment()

    with make_spinner_progress(console, transient=True) as progress:
        progress.add_task("  Running agent…")
        try:
            result = runner.run(test_input)
            error = result.error if not result.success else ""
        except Exception as exc:
            result = None
            error = str(exc)

    runner.cleanup()

    console.print()
    console.print(Rule(style="dim"))
    if error:
        console.print(
            f"\n  [bold red]✗[/bold red]  Validation failed.\n"
            f"      [dim red]{error}[/dim red]\n"
        )
        raise SystemExit(1)
    else:
        output_str = json.dumps(result.data, indent=2, default=str)
        if len(output_str) > 500:
            output_str = output_str[:497] + "..."
        console.print(
            f"\n  [bold green]✓[/bold green]  Validation passed.\n"
            f"  [dim]Output:[/dim]\n  {output_str}\n"
        )
