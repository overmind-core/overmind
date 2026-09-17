"""``overmind optimise`` — client-driven prompt/code search; server scores.

Typical loop (driven by the ``/overmind optimise`` skill):

    overmind optimise start -c <slug> -d <dataset-id>
    overmind optimise next
    overmind optimise set-template run_cmd.sh
    overmind optimise run-smoke
    overmind optimise run-baseline
    overmind optimise add-candidate --diff <file>
    overmind optimise run-iteration
    overmind optimise complete
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import overmind.config as config_mod
from overmind.optimizer import OptimiseLoop, attach_optimise, start_optimise
from overmind.optimizer_api import OptimizerAPI
from overmind.sync import resolve_api_key

OPTIMISE_HELP = "Generate candidates locally; the server scores them."

optimise_app = typer.Typer(
    name="optimise",
    help=OPTIMISE_HELP,
    no_args_is_help=True,
    invoke_without_command=False,
)
console = Console()

_STATE_FILE = Path(".overmind") / "optimise_state.json"
_DATASET_CACHE = Path(".overmind") / "datasets"


def _load_config() -> config_mod.Config:
    try:
        return config_mod.load()
    except FileNotFoundError:
        console.print("[red]overmind.toml not found. Run `overmind sync` first.[/]")
        raise typer.Exit(1) from None


def _make_api(cfg: config_mod.Config) -> OptimizerAPI:
    api_key = resolve_api_key("", cfg)
    if not api_key:
        console.print("[red]No API key. Run `overmind sync` or set OVERMIND_API_KEY.[/]")
        raise typer.Exit(1)
    base_url = os.getenv("OVERMIND_API_URL") or cfg.base_url
    return OptimizerAPI(base_url, api_key)


def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def _resolve_experiment_id(explicit: str | None) -> str:
    if explicit:
        return explicit
    state = _load_state()
    eid = state.get("experiment_id")
    if not eid:
        console.print("[red]No active experiment. Run `overmind optimise start` first, or pass --experiment <id>.[/]")
        raise typer.Exit(1)
    return eid


def _make_loop(experiment_id: str, cfg: config_mod.Config) -> OptimiseLoop:
    api = _make_api(cfg)
    state = _load_state()
    dataset_path_raw = state.get("dataset_path")
    if not dataset_path_raw:
        console.print("[red]No dataset path in local state. Re-run `overmind optimise start`.[/]")
        raise typer.Exit(1)
    return OptimiseLoop(
        api,
        experiment_id,
        repo_cwd=os.getcwd(),
        dataset_path=Path(dataset_path_raw),
        state_path=_STATE_FILE,
    )


def _print_experiment(exp: dict) -> None:
    scores = exp.get("scores") or {}
    console.print(
        Panel(
            f"[bold]Optimise[/] {exp.get('id', '')}\n"
            f"status: [cyan]{exp.get('status', '')}[/]   "
            f"mode: {exp.get('mode', 'optimize')}\n"
            f"scores: {json.dumps(scores)}",
            title="Overmind Optimise",
            border_style="blue",
        )
    )


def _print_scores(exp: dict) -> None:
    scores = exp.get("scores") or {}
    if not scores:
        console.print("scores: {}")
        return
    table = Table(title="Scores")
    table.add_column("key")
    table.add_column("value", justify="right")
    for key, value in scores.items():
        if isinstance(value, dict):
            continue
        table.add_row(str(key), f"{value}")
    console.print(table)


def _print_action(action: dict) -> None:
    name = action.get("action", "")
    exp = action.get("experiment") or {}
    _print_experiment(exp)

    if name == "WRITE_COMMAND_TEMPLATE":
        console.print("\n[bold yellow]Action: WRITE_COMMAND_TEMPLATE[/]")
        console.print("Write the command template, then:\n\n  [bold]overmind optimise set-template <file>[/]\n")
        prompt = action.get("prompt", "")
        if prompt:
            console.print(Panel(prompt, title="Prompt for your agent", border_style="yellow"))

    elif name == "RUN_SMOKE":
        console.print("\n[bold yellow]Action: RUN_SMOKE[/]")
        console.print("Run:  [bold]overmind optimise run-smoke[/]")

    elif name == "RUN_BASELINE":
        console.print("\n[bold yellow]Action: RUN_BASELINE[/]")
        console.print("Run:  [bold]overmind optimise run-baseline[/]")

    elif name == "WRITE_CANDIDATES":
        console.print("\n[bold yellow]Action: WRITE_CANDIDATES[/]")
        console.print(
            "Write unified diffs, then:\n\n"
            "  [bold]overmind optimise add-candidate --diff <file>[/]\n"
            "  [bold]overmind optimise run-iteration[/]\n"
        )
        prompt = action.get("prompt", "")
        if prompt:
            console.print(Panel(prompt, title="Prompt for your agent", border_style="yellow"))

    elif name == "RUN_ITERATION":
        pending = action.get("pending", 0)
        console.print("\n[bold yellow]Action: RUN_ITERATION[/]")
        console.print(f"Pending diffs: {pending}")
        console.print("Run:  [bold]overmind optimise run-iteration[/]")

    elif name == "WAIT":
        console.print(f"\n[bold blue]Action: WAIT[/] — {action.get('message', '')}")

    elif name == "COMPLETE":
        console.print("\n[bold green]Action: COMPLETE[/]")
        console.print(f"Scores: {json.dumps(action.get('scores') or {})}")
        console.print("Run:  [bold]overmind optimise complete[/]")

    elif name == "DONE":
        console.print("\n[bold green]DONE[/] — experiment is terminal.")
        console.print(f"Status: {exp.get('status', '')}")
        _print_scores(exp)

    else:
        console.print(f"[yellow]Unknown action: {name}[/]")


@optimise_app.command("start")
def start(
    capability: Annotated[
        str,
        typer.Option("--capability", "-c", help="Capability slug or UUID"),
    ] = "",
    dataset: Annotated[
        str,
        typer.Option("--dataset", "-d", help="Dataset UUID"),
    ] = "",
    experiment: Annotated[
        str,
        typer.Option("--experiment", "-e", help="Attach to an existing experiment instead of creating"),
    ] = "",
    eval_set: Annotated[
        str,
        typer.Option("--eval-set", help="Eval set UUID (defaults to capability active set)"),
    ] = "",
    mode: Annotated[
        str,
        typer.Option("--mode", help="optimize | hybrid"),
    ] = "optimize",
    models: Annotated[
        list[str] | None,
        typer.Option("--model", "-m", help="OpenRouter model id (hybrid only, repeatable)"),
    ] = None,
    iterations: Annotated[int, typer.Option("--iterations", help="Max candidate iterations")] = 5,
    candidates: Annotated[int, typer.Option("--candidates", help="Candidates per iteration")] = 3,
) -> None:
    """Create an optimize experiment (or attach to one) and cache the dataset locally."""
    cfg = _load_config()
    api = _make_api(cfg)
    before = experiment or _load_state().get("experiment_id", "")

    try:
        if experiment:
            exp, dataset_path, _loop = attach_optimise(
                api,
                experiment,
                cache_dir=_DATASET_CACHE,
                state_path=_STATE_FILE,
                repo_cwd=os.getcwd(),
            )
        else:
            if not capability or not dataset:
                console.print("[red]Pass --capability and --dataset, or --experiment to attach.[/]")
                raise typer.Exit(1)
            cap = cfg.capabilities.get(capability)
            cap_id = (cap.id if cap else "") or capability
            exp, dataset_path, _loop = start_optimise(
                api,
                capability_id=cap_id,
                dataset_id=dataset,
                eval_set_id=eval_set,
                cache_dir=_DATASET_CACHE,
                state_path=_STATE_FILE,
                repo_cwd=os.getcwd(),
                mode=mode,
                model_ids=models or None,
                num_iterations=iterations,
                num_candidates_per_iteration=candidates,
            )
    except Exception as exc:
        console.print(f"[red]Failed to start: {exc}[/]")
        created = _load_state().get("experiment_id", "")
        if created and created != before:
            console.print(f"Experiment {created} was created. Resume with --experiment {created}.")
        raise typer.Exit(1) from exc

    state = _load_state()
    state["dataset_path"] = str(dataset_path)
    _save_state(state)

    _print_experiment(exp)
    console.print(f"\n[green]Experiment ready:[/] {exp['id']}")
    console.print("Next:  [bold]overmind optimise next[/]")


@optimise_app.command("next")
def next_action(
    experiment: Annotated[
        str | None,
        typer.Option("--experiment", "-e", help="Experiment ID (defaults to active)"),
    ] = None,
) -> None:
    """Print the next action for the skill / agent to take."""
    cfg = _load_config()
    loop = _make_loop(_resolve_experiment_id(experiment), cfg)
    try:
        action = loop.next_action()
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/]")
        raise typer.Exit(1) from exc
    _print_action(action)


@optimise_app.command("set-template")
def set_template(
    file: Annotated[Path, typer.Argument(help="Shell command template file")],
    experiment: Annotated[
        str | None,
        typer.Option("--experiment", "-e"),
    ] = None,
) -> None:
    """Upload the command template the datapoint runner will render."""
    if not file.exists():
        console.print(f"[red]File not found: {file}[/]")
        raise typer.Exit(1)
    cfg = _load_config()
    loop = _make_loop(_resolve_experiment_id(experiment), cfg)
    try:
        exp = loop.set_template(file.read_text())
    except Exception as exc:
        console.print(f"[red]Failed to set template: {exc}[/]")
        raise typer.Exit(1) from exc
    console.print("[green]Template saved.[/]")
    _print_experiment(exp)


@optimise_app.command("run-smoke")
def run_smoke(
    experiment: Annotated[
        str | None,
        typer.Option("--experiment", "-e"),
    ] = None,
) -> None:
    """Run the first datapoint once to verify the template."""
    cfg = _load_config()
    loop = _make_loop(_resolve_experiment_id(experiment), cfg)
    try:
        result = loop.run_smoke()
    except Exception as exc:
        console.print(f"[red]Smoke failed: {exc}[/]")
        raise typer.Exit(1) from exc
    if result.get("success"):
        console.print("[green]Smoke passed.[/]")
    else:
        console.print(f"[red]Smoke failed:[/] {result.get('error', '')}")
        raise typer.Exit(1)
    if result.get("output"):
        console.print(Panel(result["output"][-2000:], title="stdout (tail)", border_style="dim"))


@optimise_app.command("run-baseline")
def run_baseline(
    experiment: Annotated[
        str | None,
        typer.Option("--experiment", "-e"),
    ] = None,
) -> None:
    """Run the full dataset against the current tree, post outputs, wait for scores."""
    cfg = _load_config()
    loop = _make_loop(_resolve_experiment_id(experiment), cfg)
    try:
        exp = loop.run_baseline()
    except Exception as exc:
        console.print(f"[red]Baseline failed: {exc}[/]")
        raise typer.Exit(1) from exc
    console.print("[green]Baseline scored.[/]")
    _print_experiment(exp)


@optimise_app.command("add-candidate")
def add_candidate(
    diff: Annotated[Path, typer.Option("--diff", help="Unified git diff file")],
    experiment: Annotated[
        str | None,
        typer.Option("--experiment", "-e"),
    ] = None,
) -> None:
    """Queue a candidate unified diff for the next iteration."""
    if not diff.exists():
        console.print(f"[red]File not found: {diff}[/]")
        raise typer.Exit(1)
    cfg = _load_config()
    loop = _make_loop(_resolve_experiment_id(experiment), cfg)
    try:
        result = loop.add_candidate_diff(diff.read_text())
    except Exception as exc:
        console.print(f"[red]Failed to add candidate: {exc}[/]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Queued.[/] pending={result['pending']}")


@optimise_app.command("run-iteration")
def run_iteration(
    experiment: Annotated[
        str | None,
        typer.Option("--experiment", "-e"),
    ] = None,
) -> None:
    """Apply queued diffs, run datapoints, post outputs, wait for scores."""
    cfg = _load_config()
    loop = _make_loop(_resolve_experiment_id(experiment), cfg)
    try:
        exp = loop.run_iteration()
    except Exception as exc:
        console.print(f"[red]Iteration failed: {exc}[/]")
        raise typer.Exit(1) from exc
    console.print("[green]Iteration scored.[/]")
    _print_experiment(exp)
    _print_scores(exp)


@optimise_app.command("status")
def status(
    experiment: Annotated[
        str | None,
        typer.Option("--experiment", "-e"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show experiment status and scores."""
    cfg = _load_config()
    loop = _make_loop(_resolve_experiment_id(experiment), cfg)
    exp = loop.status()
    if as_json:
        console.print_json(data=exp)
        return
    _print_experiment(exp)
    _print_scores(exp)


@optimise_app.command("complete")
def complete(
    experiment: Annotated[
        str | None,
        typer.Option("--experiment", "-e"),
    ] = None,
) -> None:
    """Seal the experiment and record the winner on the server."""
    cfg = _load_config()
    loop = _make_loop(_resolve_experiment_id(experiment), cfg)
    try:
        exp = loop.complete()
    except Exception as exc:
        console.print(f"[red]Complete failed: {exc}[/]")
        raise typer.Exit(1) from exc
    console.print("[green]Experiment completed.[/]")
    _print_experiment(exp)
    _print_scores(exp)
    winner = (exp.get("state") or {}).get("winner_candidate_id")
    if winner:
        console.print(f"Winner: [bold]{winner}[/]")
