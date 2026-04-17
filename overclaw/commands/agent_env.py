"""Agent environment and instrumentation helpers shared by register and setup.

Extracted from ``setup_cmd`` so that ``agent_cmd`` can collect env vars and
instrument the agent source at register time, and ``setup_cmd`` can reuse or
skip those steps when they've already been performed.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import dotenv_values
from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt
from rich.rule import Rule

from overclaw.utils.display import BRAND, confirm_option, rel, select_option
from overclaw.utils.io import read_api_key_masked
from overclaw.core.constants import overclaw_rel
from overclaw.core.paths import (
    agent_env_path,
    agent_instrumented_dir,
)
from overclaw.core.registry import project_root_from_agent_file


# ---------------------------------------------------------------------------
# Agent .env helpers
# ---------------------------------------------------------------------------


def write_agent_env(path: Path, agent_name: str, env_vars: dict[str, str]) -> None:
    """Write agent-specific env vars to ``.overclaw/agents/<name>/.env``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# OverClaw agent env — {agent_name}", ""]
    for key, val in env_vars.items():
        lines.append(f"{key}={val}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def describe_configured_agent_llm_provider(existing: dict[str, str]) -> str | None:
    """Summarize LLM provider from env keys (no secret values)."""
    oai_key = (existing.get("OPENAI_API_KEY") or "").strip()
    ant_key = (existing.get("ANTHROPIC_API_KEY") or "").strip()
    base_url = (existing.get("OPENAI_BASE_URL") or "").strip()
    analyzer = (existing.get("ANALYZER_MODEL") or "").strip()

    label: str | None = None

    if base_url and oai_key:
        label = "Other (OpenAI-compatible SDK, custom base URL)"
    elif analyzer and "/" in analyzer:
        prefix, _, _rest = analyzer.partition("/")
        pl = prefix.lower()
        if pl == "anthropic":
            label = "Anthropic"
        elif pl == "openai":
            label = "OpenAI"
        else:
            label = f"Provider {prefix}"
    elif oai_key and ant_key:
        label = "OpenAI and Anthropic"
    elif ant_key:
        label = "Anthropic"
    elif oai_key:
        label = "OpenAI"

    if label is None:
        if analyzer:
            return f"Analyzer model: {analyzer}"
        return None
    return label


def collect_agent_provider_config(agent_name: str, console: Console) -> None:
    """Ask which LLM provider the agent uses and save credentials to its per-agent .env."""
    env_path = agent_env_path(agent_name)

    if env_path.exists() and env_path.stat().st_size > 0:
        existing = {
            k: v
            for k, v in (dotenv_values(env_path) or {}).items()
            if (v or "").strip()
        }
        if existing:
            console.print(
                f"\n  [dim]Agent env already configured at [cyan]{rel(env_path)}[/cyan] "
                f"({len(existing)} variable(s) set).[/dim]"
            )
            provider_hint = describe_configured_agent_llm_provider(existing)
            if provider_hint:
                console.print(
                    f"  [dim]Looks like this agent is set up for:[/dim] "
                    f"{escape(provider_hint)}"
                )
            if not confirm_option(
                "Reconfigure agent model provider?", default=False, console=console
            ):
                return

    console.print()
    console.print(Rule(style="dim"))
    console.print(Rule("[bold]Agent model provider[/bold]", style=BRAND))
    console.print(
        "  [dim]Which provider does your agent use to call its LLM?\n"
        "  Credentials are saved to [cyan]"
        + overclaw_rel(f"agents/{agent_name}/.env")
        + "[/cyan] and loaded automatically when the agent runs.[/dim]"
    )

    pick = select_option(
        ["OpenAI", "Anthropic", "Other"],
        title="Select provider:",
        default_index=0,
        console=console,
    )

    if pick == 0:  # OpenAI
        existing_key = os.getenv("OPENAI_API_KEY", "").strip()
        if existing_key:
            console.print(
                f"\n  [dim]OPENAI_API_KEY is already set in "
                f"{overclaw_rel('.env')} — using it for this agent.[/dim]"
            )
            key = existing_key
        else:
            console.print("\n  [dim]Enter your OpenAI API key for this agent.[/dim]")
            key = read_api_key_masked("OPENAI_API_KEY")
        write_agent_env(env_path, agent_name, {"OPENAI_API_KEY": key})
        console.print(
            f"  [bold green]\u2713[/bold green] Saved [bold]OPENAI_API_KEY[/bold]"
            f" \u2192 [dim]{rel(env_path)}[/dim]"
        )

    elif pick == 1:  # Anthropic
        existing_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if existing_key:
            console.print(
                f"\n  [dim]ANTHROPIC_API_KEY is already set in "
                f"{overclaw_rel('.env')} — using it for this agent.[/dim]"
            )
            key = existing_key
        else:
            console.print("\n  [dim]Enter your Anthropic API key for this agent.[/dim]")
            key = read_api_key_masked("ANTHROPIC_API_KEY")
        write_agent_env(env_path, agent_name, {"ANTHROPIC_API_KEY": key})
        console.print(
            f"  [bold green]\u2713[/bold green] Saved [bold]ANTHROPIC_API_KEY[/bold]"
            f" \u2192 [dim]{rel(env_path)}[/dim]"
        )

    else:  # Other provider
        if confirm_option(
            "Is your provider compatible with the OpenAI SDK?",
            default=True,
            console=console,
        ):
            console.print(
                "\n  [dim]Enter the base URL for your OpenAI-compatible endpoint "
                "(e.g. https://api.example.com/v1).[/dim]"
            )
            base_url = Prompt.ask("  OPENAI_BASE_URL").strip()
            console.print("  [dim]Enter the API key for your provider.[/dim]")
            key = read_api_key_masked("OPENAI_API_KEY")
            write_agent_env(
                env_path,
                agent_name,
                {"OPENAI_BASE_URL": base_url, "OPENAI_API_KEY": key},
            )
            console.print(
                f"  [bold green]\u2713[/bold green] Saved [bold]OPENAI_BASE_URL[/bold] and "
                f"[bold]OPENAI_API_KEY[/bold] \u2192 [dim]{rel(env_path)}[/dim]"
            )
        else:
            write_agent_env(env_path, agent_name, {})
            console.print(
                f"\n  [yellow]Created[/yellow] [bold]{env_path}[/bold]\n\n"
                "  [dim]Open that file and add any environment variables your agent\n"
                "  needs to call its LLM \u2014 API keys, base URLs, custom tokens, etc.\n\n"
                "  Example:\n"
                "    MY_PROVIDER_API_KEY=sk-...\n"
                "    MY_PROVIDER_BASE_URL=https://api.example.com/v1[/dim]"
            )
            confirm_option(
                "Confirm once you've added your env variables to the file"
                " (select Yes to continue \u2014 safe to skip if no env vars are needed)",
                default=True,
                console=console,
            )


# ---------------------------------------------------------------------------
# Source instrumentation (copy agent tree into .overclaw/)
# ---------------------------------------------------------------------------

_SKIP_DIRS = {
    ".venv",
    "venv",
    "node_modules",
    ".overclaw_runners",
    "__pycache__",
    ".git",
    ".overclaw",
}


def instrument_agent_files(
    agent_path: str, agent_name: str, console: Console
) -> tuple[str, Path]:
    """Copy the agent's source tree to ``.overclaw/agents/<name>/instrumented/``.

    The original files are never modified.  This is a **plain copy** — no
    ``@observe()`` decorators or overmind-sdk imports are added here.
    Instrumentation (imports + decorators) is applied later by the
    optimizer when it actually needs tracing.

    The copy boundary is the **project root** (the directory containing
    ``.overclaw/``), not just the entry file's parent.  This ensures that
    local imports across sibling packages are available in the copy.

    Returns ``(instrumented_entry_path, instrumented_root_dir)``.
    """
    p = Path(agent_path).resolve()
    dest_dir = agent_instrumented_dir(agent_name)
    if not p.exists():
        return agent_path, dest_dir

    pr = project_root_from_agent_file(agent_path)
    copy_root = pr if pr is not None else p.parent
    entry_relpath = p.relative_to(copy_root)

    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    file_count = 0
    for src_file in copy_root.rglob("*"):
        if any(part in _SKIP_DIRS for part in src_file.parts):
            continue
        if src_file.is_dir():
            continue
        rel_path = src_file.relative_to(copy_root)
        dst_file = dest_dir / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        file_count += 1

    instrumented_entry = str(dest_dir / entry_relpath)
    console.print(
        f"  [bold green]\u2713[/bold green] Copied agent source "
        f"({file_count} file(s)) to [dim]{rel(dest_dir)}[/dim]"
    )
    return instrumented_entry, dest_dir
