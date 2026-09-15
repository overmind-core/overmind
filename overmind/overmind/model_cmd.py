"""Commands for working with deployed model artifacts."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import requests
import typer
from rich.console import Console

from overmind.config import DEFAULT_PATH, Config, load
from overmind.sync import resolve_api_key, resolve_api_url

CHECKPOINTS_PATH = "/api/deployed-models/{deployment_id}/checkpoints/"
CHECKPOINT_CHUNK_SIZE = 8_192
METADATA_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 120
WINDOWS_RESERVED_BASENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

model_app = typer.Typer(help="Manage deployed models.")
console = Console()


class CheckpointDownloadError(Exception):
    """A concise local or server-side checkpoint download failure."""


def _safe_filename(name: object) -> str:
    candidate = name if isinstance(name, str) else ""
    basename = Path(candidate.replace("\\", "/")).name
    basename = (
        ""
        .join(
            "_" if ord(character) < 32 or ord(character) == 127 or character in '<>:"|?*' else character
            for character in basename
        )
        .strip()
        .rstrip(" .")
    )
    if basename.partition(".")[0].upper() in WINDOWS_RESERVED_BASENAMES:
        basename = f"_{basename}"
    return basename if basename not in {"", ".", ".."} else "checkpoint.zip"


def _check_response(response: requests.Response, operation: str) -> None:
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int) or status_code >= 400:
        suffix = f" (HTTP {status_code})" if isinstance(status_code, int) else ""
        raise CheckpointDownloadError(f"{operation} failed{suffix}.")


def _close(response: requests.Response | None) -> None:
    if response is not None:
        close = getattr(response, "close", None)
        if close is not None:
            close()


def download_checkpoint(
    deployment_id: str,
    *,
    output: Path | None,
    api_key: str,
    api_url: str,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Download the archived checkpoint for a deployed model to a new file."""
    deployment_id = deployment_id.strip()
    if not deployment_id:
        raise CheckpointDownloadError("Deployment id is required.")

    destination = Path(output) if output is not None else None
    if destination is not None and destination.exists():
        raise CheckpointDownloadError(f"Output path already exists: {destination}")

    owns_session = session is None
    client = session or requests.Session()
    metadata_response = None
    artifact_response = None
    created = False
    bytes_written = 0
    try:
        try:
            metadata_response = client.get(
                f"{api_url.rstrip('/')}{CHECKPOINTS_PATH.format(deployment_id=quote(deployment_id, safe=''))}",
                headers={"X-Api-Key": api_key},
                timeout=METADATA_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise CheckpointDownloadError("Checkpoint metadata request failed.") from exc

        _check_response(metadata_response, "Checkpoint metadata request")
        try:
            payload = metadata_response.json()
        except (TypeError, ValueError) as exc:
            raise CheckpointDownloadError("Checkpoint metadata response was invalid.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), list) or not payload["files"]:
            raise CheckpointDownloadError("Checkpoint metadata contained no downloadable file.")

        file = payload["files"][0]
        if not isinstance(file, dict):
            raise CheckpointDownloadError("Checkpoint metadata contained no downloadable file.")
        download_url = file.get("download_url")
        if not isinstance(download_url, str) or not download_url:
            raise CheckpointDownloadError("Checkpoint metadata contained no download link.")

        expected_size = file.get("size_bytes")
        if expected_size is not None and (
            isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0
        ):
            raise CheckpointDownloadError("Checkpoint metadata contained an invalid file size.")

        filename = _safe_filename(file.get("name"))
        if destination is None:
            destination = Path(filename)
        if destination.exists():
            raise CheckpointDownloadError(f"Output path already exists: {destination}")

        try:
            artifact_response = client.get(download_url, timeout=DOWNLOAD_TIMEOUT, stream=True)
        except requests.RequestException as exc:
            raise CheckpointDownloadError("Checkpoint artifact request failed.") from exc

        _check_response(artifact_response, "Checkpoint artifact request")
        try:
            with destination.open("xb") as sink:
                created = True
                for chunk in artifact_response.iter_content(chunk_size=CHECKPOINT_CHUNK_SIZE):
                    if chunk:
                        sink.write(chunk)
                        bytes_written += len(chunk)
                if expected_size is not None and bytes_written != expected_size:
                    raise CheckpointDownloadError(
                        f"Checkpoint artifact size mismatch: expected {expected_size} bytes, received {bytes_written}."
                    )
        except requests.RequestException as exc:
            raise CheckpointDownloadError("Checkpoint artifact download failed.") from exc
        except OSError as exc:
            raise CheckpointDownloadError(f"Cannot write checkpoint to {destination}: {exc.strerror or exc}") from exc
    except CheckpointDownloadError:
        if created and destination is not None:
            with suppress(OSError):
                destination.unlink()
        raise
    finally:
        _close(metadata_response)
        _close(artifact_response)
        if owns_session:
            close_session = getattr(client, "close", None)
            if close_session is not None:
                close_session()

    result: dict[str, Any] = {
        "path": str(destination),
        "deployment_id": deployment_id,
        "filename": filename,
        "bytes_written": bytes_written,
    }
    if expected_size is not None:
        result["expected_size"] = expected_size
    return result


def _emit_error(error: str, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps({"error": error}, ensure_ascii=False))
    else:
        console.print(f"[red]{error}[/red]")


@model_app.command("download-checkpoint")
def download_checkpoint_command(
    deployment: Annotated[str, typer.Argument(help="Deployed model id supplied by MCP")],
    output: Annotated[Path | None, typer.Option("--output", help="Local output path")] = None,
    api_key: Annotated[
        str,
        typer.Option("--api-key", envvar="OVERMIND_API_KEY", help="Overmind API key", show_default=False),
    ] = "",
    api_url: Annotated[
        str,
        typer.Option("--api-url", envvar="OVERMIND_API_URL", help="Overmind API base URL"),
    ] = "",
    path: Annotated[Path, typer.Option("--path", help="Path to overmind.toml")] = DEFAULT_PATH,
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable output")] = False,
) -> None:
    """Download a deployed model checkpoint to a new local file."""
    try:
        config = load(path) if path.exists() else Config()
        key = resolve_api_key(api_key, config)
        if not key:
            raise CheckpointDownloadError("Missing API key. Pass --api-key or set OVERMIND_API_KEY.")
        result = download_checkpoint(
            deployment,
            output=output,
            api_key=key,
            api_url=resolve_api_url(api_url, config),
        )
    except (CheckpointDownloadError, OSError, ValueError) as exc:
        _emit_error(str(exc), as_json=as_json)
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    console.print(f"Downloaded {result['filename']} to {result['path']} ({result['bytes_written']} bytes).")
