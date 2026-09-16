"""Add a tracing connector using provider credentials from env or a TTY prompt."""

from __future__ import annotations

import json
import os
import sys
from getpass import getpass
from pathlib import Path
from typing import Annotated, Any

import requests
import typer
from rich.console import Console

from overmind.config import DEFAULT_PATH, Config, load
from overmind.sync import resolve_api_key, resolve_api_url

CREDENTIALS_PATH = "/api/connector-credentials/"
DEFAULT_TIMEOUT = 60
# Must match overbae.services.connectors.registry.registered_sources().
SUPPORTED_TYPES = ("langfuse", "langsmith", "braintrust", "galileo")
PAIR_TYPES = frozenset({"langfuse"})
PROVIDER_ENV = {
    "langfuse": ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"),
    "langsmith": ("LANGSMITH_API_KEY", ""),
    "braintrust": ("BRAINTRUST_API_KEY", ""),
    "galileo": ("GALILEO_API_KEY", ""),
}
GENERIC_KEY_ENV = "OVERMIND_CONNECTOR_API_KEY"
GENERIC_PAIR_ENV = "OVERMIND_CONNECTOR_API_SECRET"

connector_app = typer.Typer(help="Connect an external trace source.")
console = Console()


class ConnectorAddError(Exception):
    """A concise local or server-side connector add failure."""


def _response_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        detail = payload.get("detail")
        if isinstance(detail, dict):
            return str(detail)[:400]
        if detail:
            return str(detail)[:400]
    text = str(getattr(response, "text", "") or "").strip()
    return text[:400] or "request failed"


def _provider_credentials(connector_type: str) -> tuple[str, str]:
    primary_env, secondary_env = PROVIDER_ENV[connector_type]
    key = os.environ.get(primary_env, "").strip() or os.environ.get(GENERIC_KEY_ENV, "").strip()
    secret = ""
    if connector_type in PAIR_TYPES:
        secret = os.environ.get(secondary_env, "").strip() or os.environ.get(GENERIC_PAIR_ENV, "").strip()
    pair = connector_type in PAIR_TYPES
    if key and (not pair or secret):
        return key, secret
    if not sys.stdin.isatty():
        needed = [primary_env, secondary_env] if pair else [primary_env]
        names = " and ".join(name for name in needed if name)
        raise ConnectorAddError(f"Set {names} in this terminal, then rerun. Do not paste them into chat.")
    if not key:
        prompt = f"{connector_type} public key: " if pair else f"{connector_type} API key: "
        key = getpass(prompt).strip()
    if pair and not secret:
        secret = getpass(f"{connector_type} secret key: ").strip()
    if not key or (pair and not secret):
        raise ConnectorAddError("Provider credentials are required.")
    return key, secret


def _safe_result(payload: dict[str, Any], connector_type: str) -> dict[str, Any]:
    connector_id = str(payload.get("id") or "")
    if not connector_id:
        raise ConnectorAddError("create connector returned no id.")
    return {
        "id": connector_id,
        "name": str(payload.get("name") or "")[:255],
        "connector_type": str(payload.get("connector_type") or connector_type),
        "verified": bool(payload.get("verified_at")),
        "next_mcp_calls": [
            {
                "tool": "inspect_connectors",
                "arguments": {"connector": connector_id, "include_source_projects": True},
            },
            {"tool": "configure_connector", "arguments": {"connector": connector_id}},
            {"tool": "sync_connector", "arguments": {"connector": connector_id}},
        ],
    }


def add_connector(
    connector_type: str,
    *,
    project_id: str,
    api_key: str,
    api_url: str,
    name: str = "",
    base_url: str = "",
    provider_key: str,
    provider_secret: str = "",
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """POST provider credentials to /api/connector-credentials/ and return a secret-free result."""
    if connector_type not in SUPPORTED_TYPES:
        raise ConnectorAddError(
            f"Unsupported connector type {connector_type!r}. Use one of: {', '.join(SUPPORTED_TYPES)}."
        )
    owns_session = session is None
    client = session or requests.Session()
    client.headers.update({"X-Api-Key": api_key, "Content-Type": "application/json"})
    body = {
        "project": project_id,
        "name": (name or connector_type).strip() or connector_type,
        "connector_type": connector_type,
        "api_key": provider_key,
        "api_secret": provider_secret,
        "base_url": base_url.strip(),
        "auto_sync_enabled": False,
    }
    try:
        response = client.post(
            f"{api_url.rstrip('/')}{CREDENTIALS_PATH}",
            json=body,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ConnectorAddError(f"create connector failed: {exc}") from exc
    finally:
        if owns_session:
            client.close()
    if not response.ok:
        raise ConnectorAddError(f"HTTP {response.status_code}: {_response_detail(response)}")
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise ConnectorAddError("create connector returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise ConnectorAddError("create connector returned invalid JSON.")
    return _safe_result(payload, connector_type)


def _emit_error(error: str, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps({"error": error}, ensure_ascii=False))
    else:
        console.print(f"[red]{error}[/red]")


@connector_app.command("add")
def add(
    connector_type: Annotated[str, typer.Argument(help="langfuse, langsmith, braintrust, or galileo")],
    project_id: Annotated[str, typer.Option("--project-id", help="Project UUID")] = "",
    api_key: Annotated[
        str,
        typer.Option(
            "--api-key",
            envvar="OVERMIND_API_KEY",
            help="Overmind API key",
            show_default=False,
        ),
    ] = "",
    api_url: Annotated[
        str,
        typer.Option("--api-url", envvar="OVERMIND_API_URL", help="Overmind backend base URL"),
    ] = "",
    path: Annotated[Path, typer.Option("--path", help="Path to overmind.toml")] = DEFAULT_PATH,
    name: Annotated[str, typer.Option("--name", help="Connector name")] = "",
    base_url: Annotated[str, typer.Option("--base-url", help="Provider API base URL")] = "",
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable output")] = False,
) -> None:
    """Create a connector from provider credentials in env or a TTY prompt."""
    try:
        kind = connector_type.strip().lower()
        if kind not in SUPPORTED_TYPES:
            raise ConnectorAddError(
                f"Unsupported connector type {connector_type!r}. Use one of: {', '.join(SUPPORTED_TYPES)}."
            )
        config = load(path) if path.exists() else Config()
        key = resolve_api_key(api_key, config)
        if not key:
            raise ConnectorAddError("Missing API key. Pass --api-key or set OVERMIND_API_KEY.")
        project = project_id.strip() or config.project_id.strip()
        if not project:
            raise ConnectorAddError("Missing project-id. Pass --project-id or add project-id to overmind.toml.")
        url = resolve_api_url(api_url, config)
        host = base_url.strip() or (os.environ.get("LANGFUSE_HOST", "").strip() if kind == "langfuse" else "")
        provider_key, provider_secret = _provider_credentials(kind)
        result = add_connector(
            kind,
            project_id=project,
            api_key=key,
            api_url=url,
            name=name,
            base_url=host,
            provider_key=provider_key,
            provider_secret=provider_secret,
        )
    except (ConnectorAddError, OSError, ValueError) as exc:
        _emit_error(str(exc), as_json=as_json)
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    console.print(f"Connector {result['name']} ({result['id']}) saved.")
    console.print("Next: inspect_connectors, configure_connector, sync_connector.")
