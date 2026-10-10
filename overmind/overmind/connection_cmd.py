import json
import os
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from overmind.config import DEFAULT_PATH, load
from overmind.init_cmd import write_codex_mcp
from overmind.transfer_connection import check_connection, resolve_transfer_connection

connection_app = typer.Typer(help="Verify MCP and local file-transfer access together.")


@connection_app.command("configure")
def configure(
    project_id: Annotated[str, typer.Option("--project-id")],
    api_url: Annotated[str, typer.Option("--api-url", envvar="OVERMIND_API_URL")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
):
    """Configure global Codex MCP and local transfer with one account connection."""
    try:
        key, base = resolve_transfer_connection("", api_url, None)
        report = check_connection(api_key=key, api_url=base, project_id=project_id)
        if report["ready"] and report["transfer"].get("credential_scope") != "account":
            raise ValueError("Global setup requires an account key; project keys stay project-scoped.")
        if report["ready"]:
            profile = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "overmind/connection.toml"
            profile.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=profile.parent, prefix=".connection-")
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(f"api-key = {json.dumps(key)}\nbase-url = {json.dumps(base)}\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                write_codex_mcp(Path.home() / ".codex/config.toml", base + "/api/mcp/", key)
                os.replace(temporary, profile)
            finally:
                Path(temporary).unlink(missing_ok=True)
            report.update(configured=True, mcp_client_reload_required=True)
    except (ValueError, OSError) as exc:
        report = {"ready": False, "error": {"code": "connection_configuration", "message": str(exc)}}
    typer.echo(
        json.dumps(report)
        if as_json
        else "Connection configured; reload MCP in Codex. Permissions and readiness apply to this execution environment."
        if report["ready"]
        else f"Not configured: {report['error']['message']}"
    )
    if not report["ready"]:
        raise typer.Exit(1)


@connection_app.command("check")
def check(
    project_id: Annotated[str, typer.Option("--project-id")] = "",
    api_key: Annotated[str, typer.Option("--api-key", envvar="OVERMIND_API_KEY", show_default=False)] = "",
    api_url: Annotated[str, typer.Option("--api-url", envvar="OVERMIND_API_URL")] = "",
    path: Annotated[Path, typer.Option("--path")] = DEFAULT_PATH,
    as_json: Annotated[bool, typer.Option("--json")] = False,
):
    try:
        config = load(path) if path.exists() else None
        key, base = resolve_transfer_connection(api_key, api_url, config)
        project = project_id or (config.project_id if config else "")
        if not project:
            raise ValueError("Pass --project-id from MCP list_projects.")
        result = check_connection(api_key=key, api_url=base, project_id=project)
    except (ValueError, OSError) as exc:
        result = {"ready": False, "error": {"code": "connection_configuration", "message": str(exc)}}
    if as_json:
        typer.echo(json.dumps(result))
    elif result["ready"]:
        typer.echo("MCP and local transfer ready in this execution environment.")
    else:
        typer.echo(f"Not ready: {result['error']['message']}")
    if not result["ready"]:
        raise typer.Exit(1)
