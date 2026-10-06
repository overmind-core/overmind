"""Commands for working with deployed model artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import requests
import typer

from overmind.api import Connection, close_quietly, safe_filename, stream_to_new_file
from overmind.cli import ApiKeyOption, ApiUrlOption, ConfigPathOption, JsonOption, console, emit_json, guard
from overmind.config import DEFAULT_PATH

CHECKPOINTS_PATH = "/api/deployed-models/{deployment_id}/checkpoints/"
CHECKPOINT_CHUNK_SIZE = 8_192
METADATA_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 120

model_app = typer.Typer(help="Manage deployed models.")


class CheckpointDownloadError(Exception):
    """A concise local or server-side checkpoint download failure."""


def _check_response(response: requests.Response, operation: str) -> None:
    # The body is never echoed: checkpoint responses carry presigned URLs.
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int) or status_code >= 400:
        suffix = f" (HTTP {status_code})" if isinstance(status_code, int) else ""
        raise CheckpointDownloadError(f"{operation} failed{suffix}.")


def _checkpoint_file(response: requests.Response) -> tuple[str, str, int | None]:
    """``(download_url, filename, expected_size)`` of the first archived file."""
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise CheckpointDownloadError("Checkpoint metadata response was invalid.") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    file = files[0] if isinstance(files, list) and files else None
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
    return download_url, safe_filename(file.get("name"), fallback="checkpoint.zip"), expected_size


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
    if output is not None and output.exists():
        raise CheckpointDownloadError(f"Output path already exists: {output}")

    client = session or requests.Session()
    metadata_response = artifact_response = None
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
        download_url, filename, expected_size = _checkpoint_file(metadata_response)
        destination = output if output is not None else Path(filename)

        try:
            artifact_response = client.get(download_url, timeout=DOWNLOAD_TIMEOUT, stream=True)
        except requests.RequestException as exc:
            raise CheckpointDownloadError("Checkpoint artifact request failed.") from exc
        _check_response(artifact_response, "Checkpoint artifact request")
        bytes_written = stream_to_new_file(
            artifact_response,
            destination,
            chunk_size=CHECKPOINT_CHUNK_SIZE,
            error=CheckpointDownloadError,
            describe_stream_error=lambda _exc: "Checkpoint artifact download failed.",
            label="Checkpoint artifact",
            expected_size=expected_size,
        )
    finally:
        close_quietly(metadata_response)
        close_quietly(artifact_response)
        if session is None:
            close_quietly(client)

    result: dict[str, Any] = {
        "path": str(destination),
        "deployment_id": deployment_id,
        "filename": filename,
        "bytes_written": bytes_written,
    }
    if expected_size is not None:
        result["expected_size"] = expected_size
    return result


@model_app.command("download-checkpoint")
def download_checkpoint_command(
    deployment: Annotated[str, typer.Argument(help="Deployed model id supplied by MCP")],
    output: Annotated[Path | None, typer.Option("--output", help="Local output path")] = None,
    api_key: ApiKeyOption = "",
    api_url: ApiUrlOption = "",
    path: ConfigPathOption = DEFAULT_PATH,
    as_json: JsonOption = False,
) -> None:
    """Download a deployed model checkpoint to a new local file."""
    with guard(CheckpointDownloadError, OSError, ValueError, as_json=as_json):
        connection = Connection.resolve(path, api_key=api_key, api_url=api_url, error=CheckpointDownloadError)
        result = download_checkpoint(deployment, output=output, api_key=connection.api_key, api_url=connection.base_url)

    if as_json:
        emit_json(result)
        return
    console.print(f"Downloaded {result['filename']} to {result['path']} ({result['bytes_written']} bytes).")
