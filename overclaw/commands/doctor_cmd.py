"""overclaw doctor — read-only bundle and eval spec diagnostics."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from rich.console import Console

from overclaw.core.paths import (
    agent_instrumented_dir,
    agent_setup_spec_dir,
    load_overclaw_dotenv,
)
from overclaw.core.registry import resolve_agent
from overclaw.optimize.bundle_factory import build_agent_bundle
from overclaw.optimize.config import Config, apply_eval_spec_scope

logger = logging.getLogger("overclaw.commands.doctor")


def _detect_language(agent_path: str) -> str:
    ext = Path(agent_path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".ts": "typescript",
        ".mts": "typescript",
    }.get(ext, "python")


def main(agent_name: str) -> None:
    """Print how OverClaw resolves the bundle and eval spec (no LLM, no writes)."""
    logger.info("doctor: start agent=%s", agent_name)
    load_overclaw_dotenv()
    console = Console()

    agent_path, entrypoint_fn = resolve_agent(agent_name)
    cfg = Config(
        agent_name=agent_name,
        agent_path=agent_path,
        entrypoint_fn=entrypoint_fn,
        language=_detect_language(agent_path),
    )

    spec_path = agent_setup_spec_dir(agent_name) / "eval_spec.json"
    if spec_path.is_file():
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
        apply_eval_spec_scope(cfg, spec)
        console.print(f"[bold]Eval spec[/bold]: {spec_path}")
        scope = spec.get("scope") or {}
        for key in ("optimizable_paths", "context_paths", "exclude_paths"):
            val = scope.get(key)
            if val is not None:
                console.print(f"  [dim]{key}:[/dim] {json.dumps(val)}")
        if not any(
            scope.get(k) is not None
            for k in ("optimizable_paths", "context_paths", "exclude_paths")
        ):
            console.print("  [dim](no scope paths in spec)[/dim]")
        console.print()
    else:
        console.print(
            f"[yellow]No eval spec yet[/yellow] at {spec_path}. "
            "Run [bold]overclaw setup[/bold] to create one; using default bundle scope.\n"
        )

    console.print(f"[bold]Agent[/bold]: {agent_name}")
    console.print(f"  Entry: {agent_path}")
    console.print(f"  Entrypoint: {entrypoint_fn}")
    console.print()

    inst = agent_instrumented_dir(agent_name)
    has_inst = inst.is_dir() and any(inst.rglob("*.py"))
    if has_inst:
        console.print(f"[green]Instrumented copy:[/green] {inst}")
    else:
        console.print(
            f"[dim]Instrumented copy:[/dim] not present (expected after "
            f"[bold]overclaw setup[/bold]): {inst}"
        )
    console.print()

    bundle = build_agent_bundle(cfg)
    if bundle is None:
        console.print("[red]Could not build a file bundle from this agent.[/red]")
        return

    n_files = len(bundle.original_files)
    raw_chars = sum(len(s) for s in bundle.original_files.values())
    prompt_chars = sum(len(p.source) for p in bundle.pieces)

    console.print("[bold]Bundle[/bold]")
    console.print(f"  Resolved files: {n_files}")
    console.print(f"  On-disk characters (all files): {raw_chars}")
    console.print(f"  Prompt pieces characters (after budget): {prompt_chars}")
    console.print(
        f"  Limits: max_resolved_files={cfg.max_resolved_files}, "
        f"max_total_chars={cfg.max_total_chars}"
    )
    if cfg.optimizable_scope:
        console.print(f"  Optimizable paths: {cfg.optimizable_scope}")
    else:
        console.print("  Optimizable paths: [dim](default: entry file only)[/dim]")
    if cfg.context_scope:
        console.print(f"  Context paths: {cfg.context_scope}")
    if cfg.exclude_scope:
        console.print(f"  Exclude globs: {cfg.exclude_scope}")

    console.print("\n[dim]Read-only: no LLM calls and no file changes.[/dim]")
    logger.info("doctor: done agent=%s files=%d", agent_name, n_files)
