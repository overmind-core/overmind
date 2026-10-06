"""Add a tracing connector using provider credentials from env or a TTY prompt."""

from __future__ import annotations

import os
import sys
from getpass import getpass
from typing import Annotated, Any

import requests
import typer

from overmind.api import Connection, open_session, read_json
from overmind.cli import (
    ApiKeyOption,
    ApiUrlOption,
    ConfigPathOption,
    JsonOption,
    ProjectIdOption,
    console,
    emit_json,
    guard,
)
from overmind.config import DEFAULT_PATH

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


class ConnectorAddError(Exception):
    """A concise local or server-side connector add failure."""


def _require_supported(connector_type: str) -> None:
    if connector_type not in SUPPORTED_TYPES:
        raise ConnectorAddError(
            f"Unsupported connector type {connector_type!r}. Use one of: {', '.join(SUPPORTED_TYPES)}."
        )


def _provider_credentials(connector_type: str) -> tuple[str, str]:
    primary_env, secondary_env = PROVIDER_ENV[connector_type]
    pair = connector_type in PAIR_TYPES
    key = os.environ.get(primary_env, "").strip() or os.environ.get(GENERIC_KEY_ENV, "").strip()
    secret = ""
    if pair:
        secret = os.environ.get(secondary_env, "").strip() or os.environ.get(GENERIC_PAIR_ENV, "").strip()
    if key and (not pair or secret):
        return key, secret
    if not sys.stdin.isatty():
        needed = [primary_env, secondary_env] if pair else [primary_env]
        names = " and ".join(name for name in needed if name)
        raise ConnectorAddError(f"Set {names} in this terminal, then rerun. Do not paste them into chat.")
    if not key:
        key = getpass(f"{connector_type} public key: " if pair else f"{connector_type} API key: ").strip()
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
    """POST provider credentials to /api/connector-credentials/ and return a secret-free result.

    ``base_url`` is the provider's host, not the Overmind API.
    """
    _require_supported(connector_type)
    body = {
        "project": project_id,
        "name": (name or connector_type).strip() or connector_type,
        "connector_type": connector_type,
        "api_key": provider_key,
        "api_secret": provider_secret,
        "base_url": base_url.strip(),
        "auto_sync_enabled": False,
    }
    client = open_session(api_key, session=session)
    try:
        response = client.post(f"{api_url.rstrip('/')}{CREDENTIALS_PATH}", json=body, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise ConnectorAddError(f"create connector failed: {exc}") from exc
    finally:
        if session is None:
            client.close()
    return _safe_result(read_json(response, "create connector", error=ConnectorAddError), connector_type)


@connector_app.command("add")
def add(
    connector_type: Annotated[str, typer.Argument(help="langfuse, langsmith, braintrust, or galileo")],
    project_id: ProjectIdOption = "",
    api_key: ApiKeyOption = "",
    api_url: ApiUrlOption = "",
    path: ConfigPathOption = DEFAULT_PATH,
    name: Annotated[str, typer.Option("--name", help="Connector name")] = "",
    base_url: Annotated[str, typer.Option("--base-url", help="Provider API base URL")] = "",
    as_json: JsonOption = False,
) -> None:
    """Create a connector from provider credentials in env or a TTY prompt."""
    with guard(ConnectorAddError, OSError, ValueError, as_json=as_json):
        kind = connector_type.strip().lower()
        _require_supported(kind)
        connection = Connection.resolve(
            path, api_key=api_key, api_url=api_url, project_id=project_id, require_project=True, error=ConnectorAddError
        )
        provider_key, provider_secret = _provider_credentials(kind)
        result = add_connector(
            kind,
            project_id=connection.project_id,
            api_key=connection.api_key,
            api_url=connection.base_url,
            name=name,
            base_url=base_url.strip() or (os.environ.get("LANGFUSE_HOST", "").strip() if kind == "langfuse" else ""),
            provider_key=provider_key,
            provider_secret=provider_secret,
        )

    if as_json:
        emit_json(result)
        return
    console.print(f"Connector {result['name']} ({result['id']}) saved.")
    console.print("Next: inspect_connectors, configure_connector, sync_connector.")
