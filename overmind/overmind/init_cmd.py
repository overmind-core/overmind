"""``overmind init`` — skills, slash commands, MCP config, and seeded ``overmind.toml``."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console

from overmind.config import (
    DEFAULT_PATH,
    Config,
    default_project_name,
    dump,
    ensure_project_name,
    load,
    saved_project_api_key,
)
from overmind.skills import get_destination_dir, sync_skills
from overmind.skills_db import skills

console = Console()

Ide = Literal["cursor", "claude", "opencode", "codex"]

SUPPORTED_IDES: tuple[Ide, ...] = ("cursor", "claude", "opencode", "codex")

MCP_CONFIG_PATHS: dict[Ide, Path] = {
    "cursor": Path(".cursor/mcp.json"),
    "opencode": Path("opencode.json"),
    "codex": Path(".codex/config.toml"),
}

SIGN_IN: dict[Ide, str] = {
    "claude": "Restart Claude Code, run /mcp, select overmind, then Authenticate.",
    "codex": "Run `codex mcp login overmind`, then restart Codex.",
    "cursor": "Open Cursor Settings > MCP and click Connect next to overmind.",
    "opencode": "Run `opencode mcp auth overmind`, then restart OpenCode.",
}

MCP_URLS = {
    "production": "https://api.overmindlab.ai/api/mcp/",
    "staging": "https://api-staging.overmindlab.ai/api/mcp/",
    "development": "http://localhost:8000/api/mcp/",
    "local": "http://localhost:8000/api/mcp/",
    "dev": "http://localhost:8000/api/mcp/",
}
PRODUCTION_MCP = "https://api.overmindlab.ai/api/mcp/"
PRODUCTION_BASE = "https://api.overmindlab.ai"

# Slash-command file stem → path relative to the installed `overmind` skill.
# Filenames stay `overmind-*` so `/overmind` prefix-filters the client menu.
COMMANDS: dict[str, str] = {
    # "overmind": "SKILL.md",
    # "overmind-onboard": "references/onboard.md",
    # "overmind-setup": "references/setup.md",
    # "overmind-ensure-tracing": "../overmind-observability/SKILL.md",
    # "overmind-dataset": "../overmind-datasets/SKILL.md",
    # "overmind-finetune": "../overmind-training/SKILL.md",
    # "overmind-optimise": "../overmind-optimiser/SKILL.md",
    # "overmind-backtest": "../overmind-optimiser/SKILL.md",
}

SLASH_LABELS: dict[str, str] = {
    "overmind": "/overmind",
    "overmind-onboard": "/overmind onboard",
    "overmind-setup": "/overmind setup",
    "overmind-ensure-tracing": "/overmind ensure-tracing",
    "overmind-dataset": "/overmind dataset",
    "overmind-finetune": "/overmind finetune",
    "overmind-optimise": "/overmind optimise",
    "overmind-backtest": "/overmind backtest",
}

COMMAND_DESCRIPTIONS: dict[str, str] = {
    "overmind": "Route Overmind setup, tracing, datasets, finetune, optimise, and backtest",
    "overmind-onboard": "Connect this repository to Overmind",
    "overmind-setup": "Scan the repository and sync capabilities",
    "overmind-ensure-tracing": "Inspect traces and instrument the agent",
    "overmind-dataset": "Build, clean, upload, or export a dataset",
    "overmind-finetune": "Fine-tune, deploy, and smoke-test a model",
    "overmind-optimise": "Run prompt and code optimisation",
    "overmind-backtest": "Compare models in the repository",
}

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n*", re.DOTALL)


def _normalize_ide(ide: str) -> Ide:
    ide_norm = ide.strip().lower()
    if ide_norm in {"claude-code", "claude_code"}:
        ide_norm = "claude"
    if ide_norm not in SUPPORTED_IDES:
        console.print(f"[red]Unsupported --ide {ide!r}. Choose one of: {', '.join(SUPPORTED_IDES)}[/red]")
        raise typer.Exit(2)
    return ide_norm  # type: ignore[return-value]


def _commands_root(ide: Ide, cwd: Path) -> Path | None:
    if ide == "cursor":
        return cwd / ".cursor" / "commands"
    return None


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER.sub("", text, count=1)


def _command_body(cmd_key: str, source: Path) -> str:
    body = _strip_frontmatter(source.read_text())
    # Cursor's command palette uses the first line as the subtitle and does
    # not parse YAML, so a `---` fence shows up as "--- (project)".
    return f"{COMMAND_DESCRIPTIONS[cmd_key]}\n\n# {SLASH_LABELS[cmd_key]}\n\n{body}"


def resolve_mcp_url(env: str, api_url: str = "") -> tuple[str, str]:
    """Return ``(mcp_url, api_base_url)``.

    ``OVERMIND_API_URL`` / ``OVERMIND_BASE_URL`` win over ``--env`` so the
    console snippet (export URL, then ``overmind init``) hits the right host.
    """
    base = (
        api_url.strip()
        or os.environ.get("OVERMIND_API_URL", "").strip()
        or os.environ.get("OVERMIND_BASE_URL", "").strip()
    ).rstrip("/")
    if base:
        if base.endswith("/api/mcp"):
            return f"{base}/", base[: -len("/api/mcp")] or PRODUCTION_BASE
        return f"{base}/api/mcp/", base
    mcp = MCP_URLS.get(env, PRODUCTION_MCP)
    api_base = mcp.removesuffix("/api/mcp/").removesuffix("/api/mcp") or PRODUCTION_BASE
    return mcp, api_base


def _write_text(path: Path, content: str, *, contains_secret: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        mode = 0o600 if contains_secret else (path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def write_codex_mcp(path: Path, url: str, api_key: str | None = None) -> None:
    block = "\n".join([
        "[mcp_servers.overmind]",
        f"url = {json.dumps(url)}",
        *([f'http_headers = {{ "X-Api-Key" = {json.dumps(api_key)} }}'] if api_key else []),
    ])
    existing = path.read_text() if path.exists() else ""
    existing = re.sub(
        r"(?ms)^\[\[?mcp_servers\.overmind(?:\.[^\]]+)?\]\]?\s*\n.*?(?=^\s*\[|\Z)",
        "",
        existing,
    )
    _write_text(
        path,
        f"{existing.rstrip()}\n\n{block}\n" if existing.strip() else f"{block}\n",
        contains_secret=bool(api_key),
    )


def claude_config_path() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return (Path(config_dir).expanduser() if config_dir else Path.home()) / ".claude.json"


def write_claude_mcp(cwd: Path, url: str) -> Path:
    path = claude_config_path().resolve()
    config = json.loads(path.read_text()) if path.exists() else {}
    server = {"type": "http", "url": url}
    project = config.setdefault("projects", {}).setdefault(cwd.resolve().as_posix(), {})
    project.setdefault("mcpServers", {})["overmind"] = server
    _write_text(path, json.dumps(config, indent=2) + "\n", contains_secret=True)
    return path


def write_slash_commands(ide: Ide, cwd: Path) -> list[Path]:
    root = _commands_root(ide, cwd)
    if root is None:
        return []
    skill_root = cwd / get_destination_dir(ide) / "skills" / "overmind"
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for cmd_key, step in COMMANDS.items():
        path = root / f"{cmd_key}.md"
        path.write_text(_command_body(cmd_key, skill_root / step))
        written.append(path)
    return written


def seed_toml(path: Path, *, base_url: str) -> Path | None:
    """Create ``overmind.toml`` if missing. Backfill ``project-name`` when absent."""
    if path.exists():
        config = load(path)
        if ensure_project_name(config, path) and (not config.api_key or saved_project_api_key(path)):
            dump(config, path)
        return None
    project_id = os.environ.get("OVERMIND_PROJECT_ID", "")
    project_name = default_project_name(path)
    dump(
        Config(
            base_url=base_url,
            project_id=project_id,
            project_name=project_name,
        ),
        path,
    )
    return path


def write_mcp_config(ide: Ide, cwd: Path, mcp_url: str) -> Path:
    if ide == "claude":
        return write_claude_mcp(cwd, mcp_url)
    path = cwd / MCP_CONFIG_PATHS[ide]
    if ide == "codex":
        write_codex_mcp(path, mcp_url)
        return path
    if ide == "opencode":
        config = json.loads(path.read_text()) if path.exists() else {"$schema": "https://opencode.ai/config.json"}
        config.setdefault("mcp", {})["overmind"] = {"type": "remote", "url": mcp_url, "enabled": True}
    else:
        config = json.loads(path.read_text()) if path.exists() else {}
        config.setdefault("mcpServers", {})["overmind"] = {"url": mcp_url}
    _write_text(path, json.dumps(config, indent=2) + "\n", contains_secret=False)
    return path


def run_init(
    ide: Ide,
    *,
    cwd: Path | None = None,
    env: str = "production",
    api_url: str = "",
) -> None:
    root = (cwd or Path.cwd()).resolve()
    config_path = root / DEFAULT_PATH.name
    configured_url = api_url
    if (
        not configured_url
        and env == "production"
        and not os.environ.get("OVERMIND_API_URL")
        and not os.environ.get("OVERMIND_BASE_URL")
        and config_path.exists()
    ):
        configured_url = load(config_path).base_url
    mcp_url, base_url = resolve_mcp_url(env, configured_url)

    sync_skills([skill.slug for skill in skills], ide=ide)
    dest = get_destination_dir(ide)
    console.print(f"Skills → {dest}/skills ({len(skills)})")

    for path in write_slash_commands(ide, root):
        console.print(f"Command → {path.relative_to(root)}")

    created = seed_toml(config_path, base_url=base_url)
    mcp_path = write_mcp_config(ide, root, mcp_url)
    try:
        mcp_display = str(mcp_path.relative_to(root))
    except ValueError:
        mcp_display = str(mcp_path)
    console.print(f"MCP → {mcp_display}")

    if created:
        console.print(f"Seeded {created.relative_to(root)}")
    else:
        console.print(f"{DEFAULT_PATH.name} already present — left unchanged")

    console.print(f"\nNext: {SIGN_IN[ide]}")


def init(
    ide: Annotated[str, typer.Option(..., help="cursor, claude, claude_code, opencode or codex")],
    env: Annotated[str, typer.Option(help="production, staging or local")] = "production",
    api_url: Annotated[
        str,
        typer.Option(envvar="OVERMIND_API_URL", help="Overmind API base URL (overrides --env for MCP + toml)"),
    ] = "",
) -> None:
    """
    Install Overmind skills, slash commands, and MCP config; seed overmind.toml
    when missing. Leaves other configured MCP servers untouched.
    """
    ide_norm = _normalize_ide(ide)
    try:
        run_init(ide_norm, env=env, api_url=api_url)
    except typer.BadParameter as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
