"""Local dataset-file commands."""

from __future__ import annotations

import hashlib
import io
import json
import time
import uuid
import zipfile
from contextlib import suppress
from email.parser import Parser
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import requests
import typer
from requests.structures import CaseInsensitiveDict
from rich.console import Console

from overmind.config import DEFAULT_PATH, load
from overmind.transfer_connection import TransferError, check_connection, request_json, resolve_transfer_connection

TRANSFER_PATH = "/api/dataset-transfers/"
EXPORT_PATH = "/api/datasets/{dataset_id}/export/"
CHUNK_TIMEOUT = 120
EXPORT_CHUNK_SIZE = 8_192
ALLOWED_INTENTS = {"train", "eval", "explore"}
SPLIT_POSITIONS = ("head", "tail", "random")
EXPORT_HEADERS = (
    ("cell", "X-Overmind-Cell"),
    ("version", "X-Overmind-Version"),
    ("fingerprint", "X-Overmind-Fingerprint"),
)
WINDOWS_RESERVED_BASENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

dataset_app = typer.Typer(help="Land local files as datasets.")
console = Console()


@dataset_app.command("pipeline-upload")
def pipeline_upload(
    source: Annotated[Path, typer.Argument(exists=True, readable=True)],
    project_id: Annotated[str, typer.Option("--project-id")] = "",
    api_key: Annotated[str, typer.Option("--api-key", envvar="OVERMIND_API_KEY", show_default=False)] = "",
    api_url: Annotated[str, typer.Option("--api-url", envvar="OVERMIND_API_URL")] = "",
    path: Annotated[Path, typer.Option("--path")] = DEFAULT_PATH,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Retain a script package without registering a revision or executing it."""
    try:
        config = load(path) if path.exists() else None
        key, url = resolve_transfer_connection(api_key, api_url, config)
        project = project_id.strip() or (config.project_id.strip() if config else "")
        if not project:
            raise TransferError("Pass --project-id for the package destination.")
        if source.is_dir():
            stream = io.BytesIO()
            files = sorted(
                item
                for item in source.rglob("*")
                if not item.is_dir()
                and "__pycache__" not in item.relative_to(source).parts
                and item.suffix not in {".pyc", ".pyo"}
            )
            if not 1 <= len(files) <= 100 or any(item.is_symlink() for item in source.rglob("*")):
                raise TransferError("Use a package directory containing 1–100 regular files and no symlinks.")
            if sum(item.stat().st_size for item in files) > 10 * 1024 * 1024:
                raise TransferError("The expanded package exceeds 10 MiB.")
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in files:
                    relative = item.relative_to(source).as_posix()
                    if item.suffix not in {".py", ".json", ".txt", ".lock"} or any(
                        part.startswith(".") for part in item.relative_to(source).parts
                    ):
                        raise TransferError(
                            "Use a dedicated package directory containing Python, JSON, text and lock files only."
                        )
                    entry = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(entry, item.read_bytes())
            data = stream.getvalue()
        else:
            if source.stat().st_size > 10 * 1024 * 1024:
                raise TransferError("The package exceeds 10 MiB.")
            data = source.read_bytes()
        client = requests.Session()
        ensure_pipeline_connection(key, url, project, client)
        client.headers.update({"X-Api-Key": key})
        receipt = request_json(
            client,
            "POST",
            url.rstrip("/") + "/api/dataset-pipeline-packages/",
            stage="pipeline_package_upload",
            data={"project": project},
            files={"file": ("pipeline.zip", data, "application/zip")},
        )
        if receipt.get("sha256") != hashlib.sha256(data).hexdigest():
            raise TransferError("The retained package checksum does not match the local bytes.")
        result = {
            **receipt,
            "next_action": {
                "tool": "save_dataset_pipeline",
                "arguments": {"project_id": project, "package": receipt["id"]},
            },
            "execution": "not_run",
        }
        typer.echo(json.dumps(result) if as_json else f"Package retained: {receipt['id']}. No code was executed.")
    except (TransferError, OSError, ValueError) as exc:
        record = (
            exc.record() if isinstance(exc, TransferError) else {"code": "local_configuration", "message": str(exc)}
        )
        typer.echo(json.dumps({"error": record}) if as_json else record["message"], err=not as_json)
        raise typer.Exit(1) from None


@dataset_app.command("pipeline-download")
def pipeline_download(
    package: Annotated[str, typer.Argument()],
    output: Annotated[Path, typer.Option("--output")],
    project_id: Annotated[str, typer.Option("--project-id")],
    api_key: Annotated[str, typer.Option("--api-key", envvar="OVERMIND_API_KEY", show_default=False)] = "",
    api_url: Annotated[str, typer.Option("--api-url", envvar="OVERMIND_API_URL")] = "",
    path: Annotated[Path, typer.Option("--path")] = DEFAULT_PATH,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Download the exact retained package; refuses overwrite."""
    try:
        identity = str(uuid.UUID(package))
        config = load(path) if path.exists() else None
        key, url = resolve_transfer_connection(api_key, api_url, config)
        client = requests.Session()
        ensure_pipeline_connection(key, url, project_id, client)
        client.headers.update({"X-Api-Key": key})
        base = f"{url.rstrip('/')}/api/dataset-pipeline-packages/{identity}/"
        metadata = request_json(client, "GET", base, stage="pipeline_package_inspection")
        if metadata.get("project") != project_id:
            raise TransferError("The package does not belong to the selected project.")
        response = client.get(base + "download/", timeout=60, allow_redirects=False, stream=True)
        if 300 <= response.status_code < 400:
            raise TransferError("The package endpoint redirected; no credentials were forwarded.")
        _raise_for_status(response)
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > 10 * 1024 * 1024:
                raise TransferError("The package download exceeds 10 MiB.")
        if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise TransferError("The package download checksum does not match.")
        with output.open("xb") as destination:
            destination.write(data)
        result = {"path": str(output.resolve()), "bytes": len(data), "sha256": metadata["sha256"]}
        typer.echo(json.dumps(result) if as_json else str(output.resolve()))
    except (TransferError, OSError, ValueError, requests.RequestException) as exc:
        record = (
            exc.record()
            if isinstance(exc, TransferError)
            else {
                "code": "download_failed",
                "message": "Package download failed. Check the ID, connection and destination; existing files are never overwritten.",
            }
        )
        typer.echo(json.dumps({"error": record}) if as_json else record["message"], err=not as_json)
        raise typer.Exit(1) from None


def ensure_pipeline_connection(key, url, project, client):
    report = check_connection(api_key=key, api_url=url, project_id=project, session=client)
    if not report["ready"]:
        error = report["error"]
        raise TransferError(
            error["message"],
            code=error["code"],
            stage=error.get("stage", "connection"),
            next_action=error.get("next_action", "check_connection"),
        )


class DatasetUploadError(TransferError):
    """A concise local or server-side upload failure."""


class DatasetExportError(DatasetUploadError):
    """A concise local or server-side export failure."""


def _response_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("detail"):
            return str(payload["detail"])
    text = str(getattr(response, "text", "") or "").strip()
    return text[:400] or "request failed"


def _raise_for_status(response: requests.Response) -> None:
    if not response.ok:
        raise DatasetUploadError(f"HTTP {response.status_code}: {_response_detail(response)}")


def _json(response: requests.Response, operation: str) -> dict[str, Any]:
    _raise_for_status(response)
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise DatasetUploadError(f"{operation} returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise DatasetUploadError(f"{operation} returned invalid JSON.")
    return payload


def _required_int(payload: dict[str, Any], key: str, operation: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DatasetUploadError(f"{operation} returned no valid {key}.")
    return value


def _next_actions(*dataset_ids: str, project_id: str) -> list[dict[str, Any]]:
    return [
        action
        for dataset_id in dataset_ids
        for action in (
            {"tool": "get_job", "arguments": {"kind": "dataset_run", "id": dataset_id, "project_id": project_id}},
            {"tool": "inspect_dataset", "arguments": {"dataset": dataset_id, "project_id": project_id}},
        )
    ]


def _normalize_intent(intent: str | None) -> str | None:
    if intent is None:
        return None
    value = intent.strip()
    if not value:
        return None
    if value not in ALLOWED_INTENTS:
        raise DatasetUploadError("intent must be train, eval or explore.")
    return value


def _normalize_split(split: int | None, position: str) -> tuple[int | None, str]:
    if split is None:
        return None, position
    if not 1 <= split <= 99:
        raise DatasetUploadError("split must be between 1 and 99.")
    if position not in SPLIT_POSITIONS:
        raise DatasetUploadError("split-position must be head, tail or random.")
    return split, position


_BUSY_STATES = ("landing", "running")


def wait_until_ready(
    dataset_id: str,
    *,
    api_key: str,
    api_url: str,
    timeout: float = 3600,
    poll: float = 3,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Wait for source landing or an explicit transformation; raise on failure."""
    client = session or requests.Session()
    client.headers.update({"X-Api-Key": api_key})
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                response = client.get(f"{api_url.rstrip('/')}/api/datasets/{dataset_id}/", timeout=30)
            except requests.RequestException as exc:
                raise DatasetUploadError(f"read dataset failed: {exc}") from exc
            dataset = _json(response, "read dataset")
            state = str(dataset.get("state") or "")
            if state == "error":
                raise DatasetUploadError(str(dataset.get("error") or "The dataset failed."))
            if state not in _BUSY_STATES:
                return dataset
            if time.monotonic() > deadline:
                raise DatasetUploadError(f"Dataset {dataset_id} is still {state} after {int(timeout)}s.")
            time.sleep(poll)
    finally:
        if session is None:
            client.close()


def upload_file(
    path: Path,
    *,
    project_id: str,
    api_key: str,
    api_url: str,
    intent: str | None = None,
    capability: str | None = None,
    split: int | None = None,
    split_position: str = "tail",
    brief: str = "",
    dataset: str | None = None,
    request_key: str = "",
    json_rows_field: str = "",
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """The same file and recipe recover one durable transfer across CLI restarts."""
    intent = _normalize_intent(intent)
    split, split_position = _normalize_split(split, split_position)
    if split is not None and intent:
        raise DatasetUploadError("split fixes the intents; drop --intent.")
    if dataset:
        dataset = str(uuid.UUID(dataset))
        if split is not None or intent or capability or brief:
            raise DatasetUploadError(
                "Attaching source uses the existing dataset settings; omit brief, intent, capability and split."
            )
    capability = (capability or "").strip() or None
    owns_session = session is None
    client = session or requests.Session()
    client.headers.update({"X-Api-Key": api_key})
    base_url = api_url.rstrip("/")
    receipt = None
    try:
        readiness = check_connection(api_key=api_key, api_url=base_url, project_id=project_id, session=client)
        if not readiness["ready"]:
            error = readiness["error"]
            raise DatasetUploadError(
                error["message"],
                code=error["code"],
                stage=error.get("stage", "connection"),
                next_action=error.get("next_action", "check_connection"),
            )
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as source:
            for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(block)
                total += len(block)
        specification = {"project": project_id, "filename": path.name, "size": total, "sha256": digest.hexdigest()}
        for key, value in {
            "intent": intent,
            "capability": capability,
            "brief": brief,
            "dataset": dataset,
            "split": split,
            "json_rows_field": json_rows_field,
        }.items():
            if value not in (None, ""):
                specification[key] = value
        if split:
            specification["split_position"] = split_position
        request_key = (
            request_key
            or "file-"
            + hashlib.sha256(json.dumps(specification, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        )
        specification["request_key"] = request_key
        receipt = request_json(client, "POST", base_url + TRANSFER_PATH, stage="reserve", json=specification)
        transfer_url = base_url + TRANSFER_PATH + quote(str(receipt["id"]), safe="") + "/"
        sent = receipt.get("received")
        if isinstance(sent, bool) or not isinstance(sent, int) or not 0 <= sent <= total:
            raise DatasetUploadError("The transfer returned an invalid byte offset.")
        chunk_bytes = _required_int(receipt, "chunk_bytes", "reserve")
        if receipt["state"] == "uploading":
            with path.open("rb") as source:
                source.seek(sent)
                while sent < total:
                    chunk = source.read(min(chunk_bytes, total - sent))
                    if not chunk:
                        raise DatasetUploadError("The local file changed during transfer.", code="local_file_changed")
                    receipt = request_json(
                        client,
                        "PUT",
                        transfer_url + "chunk/",
                        stage="upload",
                        params={"offset": sent},
                        data=chunk,
                        headers={"Content-Type": "application/octet-stream"},
                    )
                    received = receipt.get("received")
                    if isinstance(received, bool) or not isinstance(received, int) or not sent < received <= total:
                        raise DatasetUploadError("The transfer returned an invalid byte offset.")
                    sent = received
                    source.seek(sent)
                if source.read(1):
                    raise DatasetUploadError("The local file changed during transfer.", code="local_file_changed")
        receipt = request_json(client, "POST", transfer_url + "complete/", stage="publication", json={})
        result = dict(receipt["result"])
        ids = [result[key] for key in ("id", "eval_id") if result.get(key)]
        if not ids:
            raise DatasetUploadError("Publication returned no dataset receipt.")
        return {
            **result,
            "state_scope": "at_publication",
            "transfer": receipt,
            "connection": readiness,
            "next_mcp_actions": _next_actions(*ids, project_id=project_id),
        }
    except TransferError as exc:
        exc.receipt = receipt or {"request_key": request_key, "project_id": project_id}
        raise
    finally:
        if owns_session:
            client.close()


def _redact_secret(message: str, secret: str) -> str:
    return message.replace(secret, "[redacted]") if secret else message


def _export_response_detail(response: requests.Response, api_key: str) -> str:
    return _redact_secret(_response_detail(response), api_key)


def _raise_export_for_status(response: requests.Response, api_key: str) -> None:
    if 300 <= response.status_code < 400:
        raise DatasetExportError(
            "The export redirect was refused; verify the configured endpoint.", code="redirect_refused"
        )
    if not response.ok:
        raise DatasetExportError(f"HTTP {response.status_code}: {_export_response_detail(response, api_key)}")


def _content_disposition_filename(header: str) -> str | None:
    if not header:
        return None
    return Parser().parsestr(f"Content-Disposition: {header}\n").get_filename()


def _safe_export_filename(filename: str | None, dataset_id: str, file_format: str) -> str:
    candidate = (filename or f"{dataset_id}.{file_format}").replace("\\", "/")
    basename = Path(candidate).name
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
    return basename if basename not in {"", ".", ".."} else f"dataset.{file_format}"


def export_dataset(
    dataset_id: str,
    *,
    file_format: str = "jsonl",
    cell: str | None = None,
    source: str | None = None,
    output: Path | None = None,
    api_key: str,
    api_url: str,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Stream the active version, or a given cell, to a new local file."""
    dataset_id = dataset_id.strip()
    file_format = file_format.strip()
    cell = cell.strip() if cell else None
    if not dataset_id:
        raise DatasetExportError("Dataset id is required.")
    if file_format not in {"jsonl", "csv"}:
        raise DatasetExportError("format must be jsonl or csv.")
    if source is not None:
        if len(source) != 64 or any(character not in "0123456789abcdef" for character in source):
            raise DatasetExportError("source must be the retained file's lowercase SHA-256 from inspect_dataset.")
        if cell or file_format != "jsonl":
            raise DatasetExportError("Choose either an original source or a formatted cell export.")

    destination = Path(output) if output is not None else None
    if destination is not None and destination.exists():
        raise DatasetExportError(f"Output path already exists: {destination}")

    owns_session = session is None
    client = session or requests.Session()
    client.headers.update({"X-Api-Key": api_key})
    response = None
    created = False
    bytes_written = 0
    digest = hashlib.sha256()
    export_headers = CaseInsensitiveDict()
    try:
        params: dict[str, str] = {"fmt": file_format}
        if cell:
            params["cell"] = cell
        endpoint = EXPORT_PATH.format(dataset_id=quote(dataset_id, safe=""))
        if source is not None:
            endpoint = f"/api/datasets/{quote(dataset_id, safe='')}/sources/{source}/"
            params = {}
        try:
            response = client.get(
                f"{api_url.rstrip('/')}{endpoint}",
                params=params,
                timeout=CHUNK_TIMEOUT,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise DatasetExportError(f"dataset export failed: {_redact_secret(str(exc), api_key)}") from exc

        _raise_export_for_status(response, api_key)
        export_headers = CaseInsensitiveDict(getattr(response, "headers", {}) or {})
        if destination is None:
            filename = _content_disposition_filename(getattr(response, "headers", {}).get("Content-Disposition", ""))
            destination = Path(_safe_export_filename(filename, dataset_id, "original" if source else file_format))
        if destination.exists():
            raise DatasetExportError(f"Output path already exists: {destination}")

        try:
            with destination.open("xb") as sink:
                created = True
                for chunk in response.iter_content(chunk_size=EXPORT_CHUNK_SIZE):
                    if chunk:
                        sink.write(chunk)
                        digest.update(chunk)
                        bytes_written += len(chunk)
            if source and digest.hexdigest() != source:
                raise DatasetExportError(
                    "Source checksum does not match its retained SHA-256; the download was removed."
                )
        except requests.RequestException as exc:
            raise DatasetExportError(f"dataset export stream failed: {_redact_secret(str(exc), api_key)}") from exc
        except OSError as exc:
            raise DatasetExportError(f"cannot write export to {destination}: {exc.strerror or exc}") from exc
    except DatasetExportError:
        if created and destination is not None:
            with suppress(OSError):
                destination.unlink()
        raise
    finally:
        if response is not None:
            close_response = getattr(response, "close", None)
            if close_response is not None:
                close_response()
        if owns_session:
            close_session = getattr(client, "close", None)
            if close_session is not None:
                close_session()

    result: dict[str, Any] = {
        "path": str(destination),
        "dataset_id": dataset_id,
        "format": "original" if source else file_format,
        "bytes_written": bytes_written,
    }
    if source:
        result.update(source=source, sha256=digest.hexdigest())
    for key, header in EXPORT_HEADERS:
        value = export_headers.get(header)
        if value:
            result[key] = str(value)
    return result


def _emit_error(error: str, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps({"error": error}, ensure_ascii=False))
    else:
        console.print(f"[red]{error}[/red]")


@dataset_app.command("upload")
def upload(
    file: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    project_id: Annotated[str, typer.Option("--project-id", help="Project UUID")] = "",
    dataset: Annotated[str | None, typer.Option("--dataset", help="Add files to an existing dataset")] = None,
    brief: Annotated[str, typer.Option("--brief", help="What to do with the source data")] = "",
    request_key: Annotated[
        str,
        typer.Option("--request-key", help="Stable transfer identity; default binds file bytes and destination recipe"),
    ] = "",
    json_rows_field: Annotated[
        str,
        typer.Option("--json-rows-field", help="Top-level JSON array field to import; original bytes remain retained"),
    ] = "",
    api_key: Annotated[
        str,
        typer.Option("--api-key", envvar="OVERMIND_API_KEY", help="Overmind API key", show_default=False),
    ] = "",
    api_url: Annotated[
        str,
        typer.Option("--api-url", envvar="OVERMIND_API_URL", help="Overmind backend base URL"),
    ] = "",
    path: Annotated[Path, typer.Option("--path", help="Path to overmind.toml")] = DEFAULT_PATH,
    intent: Annotated[
        str | None,
        typer.Option("--intent", help="Dataset intent: train, eval or explore"),
    ] = None,
    capability: Annotated[
        str | None,
        typer.Option("--capability", help="Optional capability UUID"),
    ] = None,
    split: Annotated[
        int | None,
        typer.Option("--split", help="Land a train and an eval dataset; the eval share in percent (1-99)"),
    ] = None,
    split_position: Annotated[
        str,
        typer.Option("--split-position", help="Where the eval rows come from: head, tail or random"),
    ] = "tail",
    wait: Annotated[
        bool,
        typer.Option("--wait", help="Wait for source landing; exit 1 if it fails"),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable output")] = False,
) -> None:
    """Upload FILE and land it as a dataset, or with --split as a train and an eval dataset."""
    try:
        config = load(path) if path.exists() else None
        key, url = resolve_transfer_connection(api_key, api_url, config)
        project = project_id.strip() or (config.project_id.strip() if config else "")
        if not project:
            raise DatasetUploadError("Missing project-id. Pass --project-id or add project-id to overmind.toml.")
        result = upload_file(
            file,
            project_id=project,
            api_key=key,
            api_url=url,
            intent=intent,
            capability=capability,
            split=split,
            split_position=split_position,
            dataset=dataset,
            request_key=request_key,
            json_rows_field=json_rows_field,
            brief=brief,
        )
        if wait:
            for id_key, state_key in (("id", "state"), ("eval_id", "eval_state")):
                if id_key in result:
                    ready = wait_until_ready(result[id_key], api_key=key, api_url=url)
                    result[state_key] = str(ready.get("state") or "")
            result["state_scope"] = "observed_after_landing"
    except (TransferError, OSError, ValueError) as exc:
        if as_json and isinstance(exc, TransferError):
            typer.echo(json.dumps({"error": exc.record()}, ensure_ascii=False))
        else:
            _emit_error(str(exc), as_json=as_json)
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    if wait:
        console.print(f"Dataset {result['id']} is {result['state']}.")
    else:
        console.print(f"Transfer published for {file.name}; dataset {result['id']}.")
    if "eval_id" in result:
        console.print(
            f"Eval dataset {result['eval_id']} is {result['eval_state']}."
            if wait
            else f"Eval dataset {result['eval_id']}."
        )
    if not wait:
        console.print("Next: get_job(kind=dataset_run, id=<dataset id>), then inspect_dataset.")


@dataset_app.command("export")
def export(
    dataset: Annotated[str, typer.Argument(help="Dataset id")],
    file_format: Annotated[
        str,
        typer.Option("--format", help="Export format: jsonl or csv"),
    ] = "jsonl",
    cell: Annotated[
        str | None, typer.Option("--cell", help="A cell id or a version such as 1.2; default is the active version")
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Local output path")] = None,
    source: Annotated[
        str | None, typer.Option("--source", help="Retained source SHA-256; download and verify original bytes")
    ] = None,
    api_key: Annotated[
        str,
        typer.Option("--api-key", envvar="OVERMIND_API_KEY", help="Overmind API key", show_default=False),
    ] = "",
    api_url: Annotated[
        str,
        typer.Option("--api-url", envvar="OVERMIND_API_URL", help="Overmind backend base URL"),
    ] = "",
    path: Annotated[Path, typer.Option("--path", help="Path to overmind.toml")] = DEFAULT_PATH,
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable output")] = False,
) -> None:
    """Download a dataset version to a new local file."""
    try:
        config = load(path) if path.exists() else None
        key, url = resolve_transfer_connection(api_key, api_url, config)
        result = export_dataset(
            dataset,
            file_format=file_format,
            cell=cell,
            source=source,
            output=output,
            api_key=key,
            api_url=url,
        )
    except (DatasetExportError, OSError, ValueError) as exc:
        _emit_error(str(exc), as_json=as_json)
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    console.print(f"Exported dataset {result['dataset_id']} to {result['path']} ({result['bytes_written']} bytes).")
    meta = " ".join(f"{key}={result[key]}" for key, _header in EXPORT_HEADERS if result.get(key))
    if meta:
        console.print(meta)
