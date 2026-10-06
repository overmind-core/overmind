"""``overmind sync`` — AST-scan decorator call sites and push an AgentManifest.

``overmind sync up``     Scan the repo and POST the manifest.
``overmind sync down``   GET the server snapshot (capabilities + edges).
``overmind sync``        up, then down.

``overmind.toml`` keeps only connection settings (base-url, project-id,
project-name). Capability cards are derived server-side from the manifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import requests
import typer

from overmind.api import MISSING_API_KEY, open_session, raise_for_status, resolve_api_key, resolve_api_url
from overmind.cli import console
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
from overmind.manifest import AgentManifest, scan
from overmind.slug import project_slug

SYNC_PATH = "/api/v1/sync"
PROJECTS_PATH = "/api/projects/"
API_KEYS_PATH = "/api/auth/api-keys/"
API_KEY_CURRENT_PATH = "/api/auth/api-keys/current/"

KeyScope = Literal["account", "project"]
_LOCAL_WRITE_ERRORS = (SecretFileError, OSError, ValueError)


class SyncError(Exception):
    """HTTP or local-file failure during sync."""


def _request(method: str, base_url: str, api_key: str, path: str, operation: str, **kwargs: Any) -> requests.Response:
    try:
        response = getattr(open_session(api_key), method)(f"{base_url.rstrip('/')}{path}", **kwargs)
    except requests.RequestException as exc:
        raise SyncError(f"{operation} failed: {exc}") from exc
    return response


def post_manifest(base_url: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    response = _request("post", base_url, api_key, SYNC_PATH, "sync up", json=body, timeout=120)
    raise_for_status(response, error=SyncError)
    return response.json()


def get_snapshot(base_url: str, api_key: str, project_id: str) -> dict[str, Any]:
    response = _request("get", base_url, api_key, SYNC_PATH, "sync down", params={"project_id": project_id}, timeout=60)
    raise_for_status(response, error=SyncError)
    return response.json()


def create_project(base_url: str, api_key: str, *, name: str, slug: str) -> str:
    """``POST /api/projects/``. Account-scoped keys only; project keys get 403."""
    body = {"name": name, "slug": slug, "is_active": True, "settings": {}}
    response = _request("post", base_url, api_key, PROJECTS_PATH, "create project", json=body, timeout=60)
    if response.status_code == 403:
        raise SyncError(
            "No project-id in overmind.toml, and this API key cannot create projects "
            "(project-scoped keys are pinned). Set project-id, or use an account-scoped key."
        )
    raise_for_status(response, error=SyncError)
    project_id = str((response.json() or {}).get("id") or "")
    if not project_id:
        raise SyncError("create project returned no id")
    return project_id


def mint_project_api_key(base_url: str, api_key: str, project_id: str) -> str:
    """``POST /api/auth/api-keys/`` — account-scoped keys only."""
    body = {"name": "MCP — overmind sync", "project": project_id}
    response = _request("post", base_url, api_key, API_KEYS_PATH, "mint project API key", json=body, timeout=60)
    raise_for_status(response, error=SyncError)
    key = str((response.json() or {}).get("key") or "")
    if not key:
        raise SyncError("mint project API key returned no key")
    return key


def api_key_scope(api_key: str, base_url: str) -> KeyScope:
    response = _request("get", base_url, api_key, API_KEY_CURRENT_PATH, "API key validation", timeout=30)
    raise_for_status(response, error=SyncError)
    try:
        body = response.json()
    except ValueError as exc:
        raise SyncError("API key validation returned invalid JSON") from exc
    scope = body.get("scope") if isinstance(body, dict) else None
    if scope not in {"account", "project"}:
        raise SyncError("API key validation returned an unknown scope")
    return scope


def ensure_project_id(config: Config, path: Path, api_key: str, base_url: str) -> tuple[Config, bool]:
    """Mint a project when ``project-id`` is blank and the key is account-scoped."""
    if config.project_id:
        return config, False
    if ensure_project_name(config, path):
        dump(config, path)
    name = (config.project_name or default_project_name(path))[:80]
    config.project_id = create_project(base_url, api_key, name=name, slug=project_slug(name))
    dump(config, path)
    console.print(f"Created project {name!r} → project-id={config.project_id}")
    return config, True


def ensure_mcp_project_key(
    config: Config,
    path: Path,
    api_key: str,
    base_url: str,
    *,
    key_scope: KeyScope | None = None,
) -> tuple[Config, str]:
    """Save a project-scoped key for MCP, minting one when ``api_key`` is account-scoped."""
    if not config.project_id:
        return config, api_key
    scope = key_scope or api_key_scope(api_key, base_url)
    config.api_key = mint_project_api_key(base_url, api_key, config.project_id) if scope == "account" else api_key
    dump(config, path)
    if scope == "account":
        console.print("Minted project-scoped API key for MCP.")
    return config, config.api_key


def _locate(path: Path) -> tuple[Path, Path]:
    """``(repo_root, toml_path)`` for a path that names either one."""
    if path.suffix == ".toml":
        return path.resolve().parent, path
    root = path.resolve()
    return root, root / "overmind.toml"


def _load_or_seed(toml_path: Path) -> Config:
    if toml_path.exists():
        return load(toml_path)
    config = Config()
    ensure_project_name(config, toml_path)
    dump(config, toml_path)
    console.print(f"Seeded {toml_path}")
    return config


def _scan(root: Path) -> AgentManifest:
    console.print(f"Scanning {root} for decorator declarations…")
    try:
        manifest = scan(root)
    except Exception as exc:
        raise SyncError(f"manifest scan failed: {exc}") from exc
    unresolved = [f"{s.qualname}: {', '.join(s.unresolved)}" for s in manifest.symbols if s.unresolved]
    if unresolved:
        console.print("[yellow]Non-literal decorator kwargs (skipped):[/yellow]")
        for line in unresolved[:20]:
            console.print(f"  {line}")
    return manifest


def _refresh_mcp_auth(root: Path, base_url: str, api_key: str) -> None:
    mcp_url, _ = resolve_mcp_url("production", base_url)
    try:
        updated = refresh_mcp_configs(root, mcp_url, api_key)
    except _LOCAL_WRITE_ERRORS as exc:
        raise SyncError(f"Project credential saved, but MCP config update failed: {exc}") from exc
    if updated:
        console.print(f"Updated MCP authentication: {', '.join(updated)}.")
        console.print("Reload the coding agent once. No re-export or re-init is required.")
    else:
        console.print("No Overmind MCP config found. Run `overmind init --ide <client>` to add one.")


def run_up(path: Path, api_key: str, api_url: str) -> Config:
    root, toml_path = _locate(path)
    config = _load_or_seed(toml_path)
    key = resolve_api_key(api_key, config)
    if not key:
        raise SyncError(MISSING_API_KEY)
    url = resolve_api_url(api_url, config)
    try:
        protect_secret_file(credentials_path(toml_path), repo_root=root)
        preflight_mcp_configs(root)
    except _LOCAL_WRITE_ERRORS as exc:
        raise SyncError(str(exc)) from exc

    key_scope = api_key_scope(key, url)
    if key_scope == "account":
        # An account key must never be persisted as this project's credential.
        config.api_key = ""
    try:
        config, _ = ensure_project_id(config, toml_path, key, url)
    except _LOCAL_WRITE_ERRORS as exc:
        raise SyncError(f"Could not persist the local project configuration: {exc}") from exc

    manifest = _scan(root)
    body = {**manifest.to_wire(), "project_id": config.project_id, "version": manifest.sdk_version}
    snapshot = post_manifest(url, key, body)
    console.print(
        f"Pushed {len(manifest.symbols)} symbol(s) → {len(snapshot.get('capabilities') or [])} capability(ies)"
    )

    try:
        config, key = ensure_mcp_project_key(config, toml_path, key, url, key_scope=key_scope)
    except _LOCAL_WRITE_ERRORS as exc:
        raise SyncError(f"Local sync persistence failed; a project credential may have been saved: {exc}") from exc
    _refresh_mcp_auth(root, url, key)
    return config


def run_down(path: Path, api_key: str, api_url: str) -> Config:
    _root, toml_path = _locate(path)
    config = load(toml_path) if toml_path.exists() else Config()
    key = resolve_api_key(api_key, config)
    if not key:
        raise SyncError(MISSING_API_KEY)
    if not config.project_id:
        raise SyncError(
            f"{toml_path} has no project-id (needed to fetch server state). "
            "Run `overmind sync up` first with an account-scoped key, or set project-id."
        )
    snapshot = get_snapshot(resolve_api_url(api_url, config), key, config.project_id)
    count = len(snapshot.get("capabilities") or [])
    console.print(f"Server has {count} capability(ies); cards live on the server (not written to toml).")
    return config


def run_both(path: Path, api_key: str, api_url: str) -> Config:
    config = run_up(path, api_key, api_url)
    return run_down(path, config.api_key or api_key, api_url)


_DIRECTIONS = {
    "up": (run_up, "Sync up complete"),
    "down": (run_down, "Sync down complete"),
    "both": (run_both, "Synced"),
}


def sync(
    direction: Annotated[
        str,
        typer.Argument(help="up (POST local), down (GET server), or omit for both"),
    ] = "both",
    api_key: Annotated[str, typer.Option(help="Overmind API key", show_default=False)] = "",
    api_url: Annotated[str, typer.Option(envvar="OVERMIND_API_URL", help="Overmind backend base URL")] = "",
    path: Annotated[Path, typer.Option(help="Path to overmind.toml or repo root")] = DEFAULT_PATH,
) -> None:
    """Scan decorator declarations and sync the agent graph with the server."""
    if direction not in _DIRECTIONS:
        console.print("[red]direction must be up, down, or omitted[/red]")
        raise typer.Exit(2)
    run, done = _DIRECTIONS[direction]
    try:
        run(path, api_key, api_url)
    except SyncError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(done)
