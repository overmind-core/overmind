"""Two-way ``overmind.toml`` <-> ``POST|GET /api/v1/sync``.

``overmind sync up``     POST the local snapshot; write the reconciled response.
``overmind sync down``   GET the server snapshot; overwrite the local file.
``overmind sync``        up, then down.

When ``project-id`` is missing and the API key is account-scoped, ``up`` /
``sync`` creates a project via ``POST /api/projects/`` and writes the id back
before pushing. Project-scoped keys still require an explicit ``project-id``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import requests
import typer
from rich.console import Console

from overmind.config import (
    DEFAULT_PATH,
    Config,
    SecretFileError,
    credentials_path,
    default_project_name,
    dump,
    ensure_project_name,
    load,
    protect_secret_file,
)
from overmind.init_cmd import preflight_mcp_configs, refresh_mcp_configs, resolve_mcp_url

SYNC_PATH = "/api/v1/sync"
PROJECTS_PATH = "/api/projects/"
API_KEYS_PATH = "/api/auth/api-keys/"
API_KEY_CURRENT_PATH = "/api/auth/api-keys/current/"
DEFAULT_BASE_URL = "https://api.overmindlab.ai"

console = Console()


class SyncError(Exception):
    """HTTP or local-file failure during sync."""


def _session(api_key: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"X-Api-Key": api_key, "Content-Type": "application/json"})
    return session


def _raise_for_status(resp: requests.Response) -> None:
    if resp.ok:
        return
    try:
        detail = resp.json()
        msg = detail.get("error", {}).get("message") or detail.get("detail") or resp.text[:400]
    except Exception:
        msg = resp.text[:400]
    raise SyncError(f"HTTP {resp.status_code}: {msg}")


def post_snapshot(base_url: str, api_key: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{SYNC_PATH}"
    try:
        resp = _session(api_key).post(url, json=snapshot, timeout=60)
    except requests.RequestException as exc:
        raise SyncError(f"sync up failed: {exc}") from exc
    _raise_for_status(resp)
    return resp.json()


def get_snapshot(base_url: str, api_key: str, project_id: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{SYNC_PATH}"
    try:
        resp = _session(api_key).get(url, params={"project_id": project_id}, timeout=60)
    except requests.RequestException as exc:
        raise SyncError(f"sync down failed: {exc}") from exc
    _raise_for_status(resp)
    return resp.json()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return (slug or "project")[:80]


def _project_name_hint(config: Config, path: Path) -> str:
    ensure_project_name(config, path)
    return (config.project_name or default_project_name(path))[:80]


def create_project(base_url: str, api_key: str, *, name: str, slug: str) -> str:
    """``POST /api/projects/``. Account-scoped keys only; project keys get 403."""
    url = f"{base_url.rstrip('/')}{PROJECTS_PATH}"
    body = {"name": name, "slug": slug, "is_active": True, "settings": {}}
    try:
        resp = _session(api_key).post(url, json=body, timeout=60)
    except requests.RequestException as exc:
        raise SyncError(f"create project failed: {exc}") from exc
    if resp.status_code == 403:
        raise SyncError(
            "No project-id in overmind.toml, and this API key cannot create projects "
            "(project-scoped keys are pinned). Set project-id, or use an account-scoped key."
        )
    _raise_for_status(resp)
    project_id = str((resp.json() or {}).get("id") or "")
    if not project_id:
        raise SyncError("create project returned no id")
    return project_id


def mint_project_api_key(base_url: str, api_key: str, project_id: str) -> str:
    """``POST /api/auth/api-keys/`` — account-scoped keys only."""
    url = f"{base_url.rstrip('/')}{API_KEYS_PATH}"
    body = {"name": "MCP — overmind sync", "project": project_id}
    try:
        resp = _session(api_key).post(url, json=body, timeout=60)
    except requests.RequestException as exc:
        raise SyncError(f"mint project API key failed: {exc}") from exc
    _raise_for_status(resp)
    key = str((resp.json() or {}).get("key") or "")
    if not key:
        raise SyncError("mint project API key returned no key")
    return key


def _api_key_scope(api_key: str, base_url: str) -> Literal["account", "project"]:
    url = f"{base_url.rstrip('/')}{API_KEY_CURRENT_PATH}"
    try:
        resp = _session(api_key).get(url, timeout=30)
    except requests.RequestException as exc:
        raise SyncError(f"API key validation failed: {exc}") from exc
    _raise_for_status(resp)
    try:
        body = resp.json()
    except ValueError as exc:
        raise SyncError("API key validation returned invalid JSON") from exc
    scope = body.get("scope") if isinstance(body, dict) else None
    if scope not in {"account", "project"}:
        raise SyncError("API key validation returned an unknown scope")
    return scope


def ensure_mcp_project_key(
    config: Config,
    path: Path,
    api_key: str,
    base_url: str,
    *,
    key_scope: Literal["account", "project"] | None = None,
) -> tuple[Config, str]:
    if not config.project_id:
        return config, api_key
    scope = key_scope or _api_key_scope(api_key, base_url)
    new_key = mint_project_api_key(base_url, api_key, config.project_id) if scope == "account" else api_key
    config.api_key = new_key
    dump(config, path)
    if scope == "account":
        console.print("Minted project-scoped API key for MCP.")
    return config, new_key


def ensure_project_id(config: Config, path: Path, api_key: str, base_url: str) -> tuple[Config, bool]:
    """Mint a project when ``project-id`` is blank and the key is account-scoped."""
    if config.project_id:
        return config, False
    if ensure_project_name(config, path):
        dump(config, path)
    name = _project_name_hint(config, path)
    project_id = create_project(base_url, api_key, name=name, slug=_slugify(name))
    config.project_id = project_id
    dump(config, path)
    console.print(f"Created project {name!r} → project-id={project_id}")
    return config, True


def resolve_api_key(cli_key: str, config: Config | None) -> str:
    if cli_key:
        return cli_key
    if config and config.project_id and config.api_key:
        return config.api_key
    env_key = os.environ.get("OVERMIND_API_KEY", "")
    if env_key:
        return env_key
    toml_key = (config.api_key if config else "") or ""
    if toml_key.startswith("env:"):
        toml_key = os.environ.get(toml_key[4:], "")
    return toml_key


def resolve_api_url(cli_url: str, config: Config | None) -> str:
    if cli_url:
        return cli_url.rstrip("/")
    env = os.environ.get("OVERMIND_API_URL")
    if env:
        return env.rstrip("/")
    if config and config.base_url:
        return config.base_url.rstrip("/")
    return DEFAULT_BASE_URL


def run_up(path: Path, api_key: str, api_url: str) -> Config:
    if not path.exists():
        raise SyncError(f"{path} not found — nothing to push")
    config = load(path)
    key = resolve_api_key(api_key, config)
    if not key:
        raise SyncError("Missing API key. Pass --api-key or set OVERMIND_API_KEY.")
    url = resolve_api_url(api_url, config)
    try:
        protect_secret_file(credentials_path(path), repo_root=path.resolve().parent)
        preflight_mcp_configs(path.resolve().parent)
    except (SecretFileError, OSError, ValueError) as exc:
        raise SyncError(str(exc)) from exc
    key_scope = _api_key_scope(key, url)
    if key_scope == "account":
        config.api_key = ""
    try:
        config, _ = ensure_project_id(config, path, key, url)
    except (SecretFileError, OSError, ValueError) as exc:
        raise SyncError(f"Could not persist the local project configuration: {exc}") from exc
    snapshot = post_snapshot(url, key, config.to_snapshot())
    config.apply_snapshot(snapshot)
    try:
        dump(config, path)
        config, key = ensure_mcp_project_key(config, path, key, url, key_scope=key_scope)
    except (SecretFileError, OSError, ValueError) as exc:
        raise SyncError(f"Local sync persistence failed; a project credential may have been saved: {exc}") from exc
    mcp_url, _ = resolve_mcp_url("production", url)
    try:
        updated = refresh_mcp_configs(path.resolve().parent, mcp_url, key)
    except (SecretFileError, OSError, ValueError) as exc:
        raise SyncError(f"Project credential saved, but MCP config update failed: {exc}") from exc
    if updated:
        console.print(f"Updated MCP authentication: {', '.join(updated)}.")
        console.print("Reload the coding agent once. No re-export or re-init is required.")
    else:
        console.print("No Overmind MCP config found. Run `overmind init --ide <client>` to add one.")
    return config


def run_down(path: Path, api_key: str, api_url: str) -> Config:
    config = load(path) if path.exists() else Config()
    key = resolve_api_key(api_key, config)
    if not key:
        raise SyncError("Missing API key. Pass --api-key or set OVERMIND_API_KEY.")
    if not config.project_id:
        raise SyncError(
            f"{path} has no project-id (needed to fetch server state). "
            "Run `overmind sync up` first with an account-scoped key, or set project-id."
        )
    url = resolve_api_url(api_url, config)
    snapshot = get_snapshot(url, key, config.project_id)
    config.apply_snapshot(snapshot)
    dump(config, path)
    return config


def run_both(path: Path, api_key: str, api_url: str) -> Config:
    config = run_up(path, api_key, api_url)
    return run_down(path, config.api_key or api_key, api_url)


def sync(
    direction: Annotated[
        str,
        typer.Argument(help="up (POST local), down (GET server), or omit for both"),
    ] = "both",
    api_key: Annotated[
        str,
        typer.Option(help="Overmind API key", show_default=False),
    ] = "",
    api_url: Annotated[str, typer.Option(envvar="OVERMIND_API_URL", help="Overmind backend base URL")] = "",
    path: Annotated[Path, typer.Option(help="Path to overmind.toml")] = DEFAULT_PATH,
) -> None:
    """Two-way sync of overmind.toml with POST|GET /api/v1/sync."""
    if direction not in ("both", "up", "down"):
        console.print("[red]direction must be up, down, or omitted[/red]")
        raise typer.Exit(2)
    try:
        if direction == "up":
            run_up(path, api_key, api_url)
            console.print(f"Pushed {path}")
        elif direction == "down":
            run_down(path, api_key, api_url)
            console.print(f"Pulled {path}")
        else:
            run_both(path, api_key, api_url)
            console.print(f"Synced {path}")
    except SyncError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
