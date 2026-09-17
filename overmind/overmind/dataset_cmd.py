"""Local dataset-file commands."""

from __future__ import annotations

import json
import time
from contextlib import suppress
from email.parser import Parser
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import requests
import typer
from rich.console import Console

from overmind.config import DEFAULT_PATH, Config, load
from overmind.sync import resolve_api_key, resolve_api_url

UPLOAD_PATH = "/api/uploads/"
DATASETS_PATH = "/api/datasets/"
SPLIT_PATH = "/api/datasets/split/"
EXPORT_PATH = "/api/datasets/{dataset_id}/export/"
DEFAULT_TIMEOUT = 60
CHUNK_TIMEOUT = 120
EXPORT_CHUNK_SIZE = 8_192
ALLOWED_INTENTS = {"train", "eval"}
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


class DatasetUploadError(Exception):
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


def _next_actions(*dataset_ids: str) -> list[dict[str, Any]]:
    return [
        action
        for dataset_id in dataset_ids
        for action in (
            {"tool": "get_job", "arguments": {"kind": "dataset_run", "id": dataset_id}},
            {"tool": "inspect_dataset", "arguments": {"dataset": dataset_id}},
        )
    ]


def _normalize_intent(intent: str | None) -> str | None:
    if intent is None:
        return None
    value = intent.strip()
    if not value:
        return None
    if value not in ALLOWED_INTENTS:
        raise DatasetUploadError("intent must be train or eval.")
    return value


def _normalize_split(split: int | None, position: str) -> tuple[int | None, str]:
    if split is None:
        return None, position
    if not 1 <= split <= 99:
        raise DatasetUploadError("split must be between 1 and 99.")
    if position not in SPLIT_POSITIONS:
        raise DatasetUploadError("split-position must be head, tail or random.")
    return split, position


_BUSY_STATES = ("landing", "diagnosing", "running")


def wait_until_ready(
    dataset_id: str,
    *,
    api_key: str,
    api_url: str,
    timeout: float = 3600,
    poll: float = 3,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Poll until the landing and the first scan end. An ``error`` state raises
    with the dataset's own message."""
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
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Stream one local file through /api/uploads/ then land it as a dataset, or
    with ``split`` as a train dataset and an eval dataset holding that percent."""
    intent = _normalize_intent(intent)
    split, split_position = _normalize_split(split, split_position)
    if split is not None and intent:
        raise DatasetUploadError("split fixes the intents; drop --intent.")
    capability = (capability or "").strip() or None
    try:
        total = path.stat().st_size
    except OSError as exc:
        raise DatasetUploadError(f"Cannot read {path}: {exc.strerror or exc}") from exc

    owns_session = session is None
    client = session or requests.Session()
    client.headers.update({"X-Api-Key": api_key, "Content-Type": "application/json"})
    base_url = api_url.rstrip("/")
    try:
        try:
            reserved_response = client.post(
                f"{base_url}{UPLOAD_PATH}",
                json={"filename": path.name},
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise DatasetUploadError(f"reserve upload failed: {exc}") from exc
        reserved = _json(reserved_response, "reserve upload")
        upload_id = str(reserved.get("upload_id") or "")
        if not upload_id:
            raise DatasetUploadError("reserve upload returned no upload_id.")
        chunk_bytes = _required_int(reserved, "chunk_bytes", "reserve upload")
        max_bytes = _required_int(reserved, "max_bytes", "reserve upload")
        if total > max_bytes:
            raise DatasetUploadError(f"{path.name} is {total} bytes; the server limit is {max_bytes} bytes.")

        try:
            state_response = client.get(f"{base_url}{UPLOAD_PATH}{upload_id}/", timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise DatasetUploadError(f"read upload state failed: {exc}") from exc
        state = _json(state_response, "read upload state")
        sent = state.get("received")
        if isinstance(sent, bool) or not isinstance(sent, int) or sent < 0 or sent > total:
            raise DatasetUploadError("read upload state returned an invalid byte offset.")

        with path.open("rb") as source:
            source.seek(sent)
            while sent < total:
                chunk = source.read(min(chunk_bytes, total - sent))
                if not chunk:
                    raise DatasetUploadError("local file ended before the advertised size.")
                try:
                    chunk_response = client.put(
                        f"{base_url}{UPLOAD_PATH}{upload_id}/chunk/",
                        params={"offset": sent},
                        data=chunk,
                        headers={"Content-Type": "application/octet-stream"},
                        timeout=CHUNK_TIMEOUT,
                    )
                except requests.RequestException as exc:
                    raise DatasetUploadError(f"upload chunk failed: {exc}") from exc
                chunk_state = _json(chunk_response, "upload chunk")
                received = chunk_state.get("received")
                if isinstance(received, bool) or not isinstance(received, int) or received <= sent or received > total:
                    raise DatasetUploadError("upload chunk returned an invalid byte offset.")
                sent = received
                source.seek(sent)

        body: dict[str, Any] = {
            "project": project_id,
            "name": path.name,
            "source": {"upload_id": upload_id, "filename": path.name},
        }
        if intent:
            body["intent"] = intent
        if capability:
            body["capability"] = capability
        if split is not None:
            body["eval_percent"] = split
            body["position"] = split_position
        create_path = SPLIT_PATH if split is not None else DATASETS_PATH
        try:
            dataset_response = client.post(f"{base_url}{create_path}", json=body, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise DatasetUploadError(f"create dataset failed: {exc}") from exc
        created = _json(dataset_response, "create dataset")
        dataset = created.get("train") if split is not None else created
        if not isinstance(dataset, dict):
            raise DatasetUploadError("create dataset returned no train dataset.")
        dataset_id = str(dataset.get("id") or "")
        if not dataset_id:
            raise DatasetUploadError("create dataset returned no id.")
        result = {
            "id": dataset_id,
            "state": str(dataset.get("state") or "landing"),
            "next_mcp_actions": _next_actions(dataset_id),
        }
        if split is not None:
            evaluation = created.get("eval")
            eval_id = str(evaluation.get("id") or "") if isinstance(evaluation, dict) else ""
            if not eval_id:
                raise DatasetUploadError("create dataset returned no eval dataset.")
            result["eval_id"] = eval_id
            result["next_mcp_actions"] = _next_actions(dataset_id, eval_id)
            result["eval_state"] = str(evaluation.get("state") or "landing")
        return result
    finally:
        if owns_session:
            client.close()


def _redact_secret(message: str, secret: str) -> str:
    return message.replace(secret, "[redacted]") if secret else message


def _export_response_detail(response: requests.Response, api_key: str) -> str:
    return _redact_secret(_response_detail(response), api_key)


def _raise_export_for_status(response: requests.Response, api_key: str) -> None:
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

    destination = Path(output) if output is not None else None
    if destination is not None and destination.exists():
        raise DatasetExportError(f"Output path already exists: {destination}")

    owns_session = session is None
    client = session or requests.Session()
    client.headers.update({"X-Api-Key": api_key})
    response = None
    created = False
    bytes_written = 0
    export_headers: dict[str, str] = {}
    try:
        params: dict[str, str] = {"fmt": file_format}
        if cell:
            params["cell"] = cell
        try:
            response = client.get(
                f"{api_url.rstrip('/')}{EXPORT_PATH.format(dataset_id=quote(dataset_id, safe=''))}",
                params=params,
                timeout=CHUNK_TIMEOUT,
                stream=True,
            )
        except requests.RequestException as exc:
            raise DatasetExportError(f"dataset export failed: {_redact_secret(str(exc), api_key)}") from exc

        _raise_export_for_status(response, api_key)
        export_headers = dict(getattr(response, "headers", {}) or {})
        if destination is None:
            filename = _content_disposition_filename(getattr(response, "headers", {}).get("Content-Disposition", ""))
            destination = Path(_safe_export_filename(filename, dataset_id, file_format))
        if destination.exists():
            raise DatasetExportError(f"Output path already exists: {destination}")

        try:
            with destination.open("xb") as sink:
                created = True
                for chunk in response.iter_content(chunk_size=EXPORT_CHUNK_SIZE):
                    if chunk:
                        sink.write(chunk)
                        bytes_written += len(chunk)
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
        "format": file_format,
        "bytes_written": bytes_written,
    }
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
        typer.Option("--intent", help="Dataset intent: train or eval"),
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
        typer.Option("--wait", help="Wait for the landing and the first scan; exit 1 if either fails"),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable output")] = False,
) -> None:
    """Upload FILE and land it as a dataset, or with --split as a train and an eval dataset."""
    try:
        config = load(path) if path.exists() else Config()
        key = resolve_api_key(api_key, config)
        if not key:
            raise DatasetUploadError("Missing API key. Pass --api-key or set OVERMIND_API_KEY.")
        project = project_id.strip() or config.project_id.strip()
        if not project:
            raise DatasetUploadError("Missing project-id. Pass --project-id or add project-id to overmind.toml.")
        url = resolve_api_url(api_url, config)
        result = upload_file(
            file,
            project_id=project,
            api_key=key,
            api_url=url,
            intent=intent,
            capability=capability,
            split=split,
            split_position=split_position,
        )
        if wait:
            for id_key, state_key in (("id", "state"), ("eval_id", "eval_state")):
                if id_key in result:
                    ready = wait_until_ready(result[id_key], api_key=key, api_url=url)
                    result[state_key] = str(ready.get("state") or "")
    except (DatasetUploadError, OSError, ValueError) as exc:
        _emit_error(str(exc), as_json=as_json)
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    console.print(f"Uploaded {file.name}; dataset {result['id']} is {result['state']}.")
    if "eval_id" in result:
        console.print(f"Eval dataset {result['eval_id']} is {result['eval_state']}.")
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
        config = load(path) if path.exists() else Config()
        key = resolve_api_key(api_key, config)
        if not key:
            raise DatasetExportError("Missing API key. Pass --api-key or set OVERMIND_API_KEY.")
        result = export_dataset(
            dataset,
            file_format=file_format,
            cell=cell,
            output=output,
            api_key=key,
            api_url=resolve_api_url(api_url, config),
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
