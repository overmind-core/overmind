"""Local dataset-file commands: chunked upload and streamed export."""

from __future__ import annotations

import time
from email.parser import Parser
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import requests
import typer

from overmind.api import (
    Connection,
    close_quietly,
    open_session,
    poll,
    read_json,
    response_detail,
    safe_filename,
    stream_to_new_file,
)
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

UPLOAD_PATH = "/api/uploads/"
DATASETS_PATH = "/api/datasets/"
SPLIT_PATH = "/api/datasets/split/"
EXPORT_PATH = "/api/datasets/{dataset_id}/export/"
DEFAULT_TIMEOUT = 60
CHUNK_TIMEOUT = 120
CHUNK_ATTEMPTS = 4
EXPORT_CHUNK_SIZE = 8_192
ALLOWED_INTENTS = {"train", "eval"}
SPLIT_POSITIONS = ("head", "tail", "random")
EXPORT_HEADERS = (
    ("cell", "X-Overmind-Cell"),
    ("version", "X-Overmind-Version"),
    ("fingerprint", "X-Overmind-Fingerprint"),
)
_BUSY_STATES = ("landing", "diagnosing", "running")

dataset_app = typer.Typer(help="Land local files as datasets.")


class DatasetUploadError(Exception):
    """A concise local or server-side upload failure."""


class DatasetExportError(DatasetUploadError):
    """A concise local or server-side export failure."""


def _next_actions(*dataset_ids: str) -> list[dict[str, Any]]:
    return [
        action
        for dataset_id in dataset_ids
        for action in (
            {"tool": "get_job", "arguments": {"kind": "dataset_run", "id": dataset_id}},
            {"tool": "inspect_dataset", "arguments": {"dataset": dataset_id}},
        )
    ]


def _positive_int(payload: dict[str, Any], key: str, operation: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DatasetUploadError(f"{operation} returned no valid {key}.")
    return value


def _byte_offset(payload: dict[str, Any], operation: str, *, above: int, limit: int) -> int:
    value = payload.get("received")
    if isinstance(value, bool) or not isinstance(value, int) or value <= above or value > limit:
        raise DatasetUploadError(f"{operation} returned an invalid byte offset.")
    return value


def _normalize_intent(intent: str | None) -> str | None:
    value = (intent or "").strip()
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


def wait_until_ready(
    dataset_id: str,
    *,
    api_key: str,
    api_url: str,
    timeout: float = 3600,
    poll_interval: float = 3,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Poll until the landing and the first scan end. An ``error`` state raises
    with the dataset's own message."""
    client = open_session(api_key, json_body=False, session=session)
    url = f"{api_url.rstrip('/')}/api/datasets/{dataset_id}/"

    def fetch() -> dict[str, Any]:
        try:
            response = client.get(url, timeout=30)
        except requests.RequestException as exc:
            raise DatasetUploadError(f"read dataset failed: {exc}") from exc
        return read_json(response, "read dataset", error=DatasetUploadError)

    def settled(dataset: dict[str, Any]) -> bool:
        if dataset.get("state") == "error":
            raise DatasetUploadError(str(dataset.get("error") or "The dataset failed."))
        return dataset.get("state") not in _BUSY_STATES

    try:
        return poll(
            fetch,
            settled=settled,
            deadline=time.monotonic() + timeout,
            interval=poll_interval,
            timed_out=lambda dataset: DatasetUploadError(
                f"Dataset {dataset_id} is still {dataset.get('state')} after {int(timeout)}s."
            ),
        )
    finally:
        if session is None:
            client.close()


class _Upload:
    """One file pushed through ``/api/uploads/`` in server-sized chunks.

    Resumable: the server reports how many bytes it already holds and the
    upload continues from there.
    """

    def __init__(self, client: requests.Session, base_url: str, path: Path) -> None:
        self.client = client
        self.base_url = base_url
        self.path = path
        try:
            self.size = path.stat().st_size
        except OSError as exc:
            raise DatasetUploadError(f"Cannot read {path}: {exc.strerror or exc}") from exc

    def _call(self, method: str, path: str, operation: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = getattr(self.client, method)(f"{self.base_url}{path}", **kwargs)
        except requests.RequestException as exc:
            raise DatasetUploadError(f"{operation} failed: {exc}") from exc
        return read_json(response, operation, error=DatasetUploadError)

    def reserve(self) -> tuple[str, int]:
        """``(upload_id, chunk_bytes)``; refuses a file over the server's limit."""
        reserved = self._call(
            "post", UPLOAD_PATH, "reserve upload", json={"filename": self.path.name}, timeout=DEFAULT_TIMEOUT
        )
        upload_id = str(reserved.get("upload_id") or "")
        if not upload_id:
            raise DatasetUploadError("reserve upload returned no upload_id.")
        chunk_bytes = _positive_int(reserved, "chunk_bytes", "reserve upload")
        max_bytes = _positive_int(reserved, "max_bytes", "reserve upload")
        if self.size > max_bytes:
            raise DatasetUploadError(f"{self.path.name} is {self.size} bytes; the server limit is {max_bytes} bytes.")
        return upload_id, chunk_bytes

    def transfer(self, upload_id: str, chunk_bytes: int) -> None:
        state = self._call("get", f"{UPLOAD_PATH}{upload_id}/", "read upload state", timeout=DEFAULT_TIMEOUT)
        sent = _byte_offset(state, "read upload state", above=-1, limit=self.size)
        with self.path.open("rb") as source:
            source.seek(sent)
            while sent < self.size:
                chunk = source.read(min(chunk_bytes, self.size - sent))
                if not chunk:
                    raise DatasetUploadError("local file ended before the advertised size.")
                sent = self._send_chunk(upload_id, sent, chunk)
                source.seek(sent)

    def _send_chunk(self, upload_id: str, offset: int, chunk: bytes) -> int:
        # The server stores a chunk once however often it is sent, so a dropped
        # connection is answered by sending the same bytes again.
        for attempt in range(CHUNK_ATTEMPTS):
            try:
                response = self.client.put(
                    f"{self.base_url}{UPLOAD_PATH}{upload_id}/chunk/",
                    params={"offset": offset},
                    data=chunk,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=CHUNK_TIMEOUT,
                )
                break
            except requests.RequestException as exc:
                if attempt == CHUNK_ATTEMPTS - 1:
                    raise DatasetUploadError(f"upload chunk failed: {exc}") from exc
                time.sleep(2**attempt)
        state = read_json(response, "upload chunk", error=DatasetUploadError)
        return _byte_offset(state, "upload chunk", above=offset, limit=self.size)

    def create(self, body: dict[str, Any], *, split: bool) -> dict[str, Any]:
        path = SPLIT_PATH if split else DATASETS_PATH
        return self._call("post", path, "create dataset", json=body, timeout=DEFAULT_TIMEOUT)


def _landed(created: dict[str, Any], *, split: bool) -> dict[str, Any]:
    dataset = created.get("train") if split else created
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
    if split:
        evaluation = created.get("eval")
        eval_id = str(evaluation.get("id") or "") if isinstance(evaluation, dict) else ""
        if not eval_id:
            raise DatasetUploadError("create dataset returned no eval dataset.")
        result["eval_id"] = eval_id
        result["eval_state"] = str(evaluation.get("state") or "landing")
        result["next_mcp_actions"] = _next_actions(dataset_id, eval_id)
    return result


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

    upload = _Upload(open_session(api_key, session=session), api_url.rstrip("/"), path)
    try:
        upload_id, chunk_bytes = upload.reserve()
        upload.transfer(upload_id, chunk_bytes)
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
        return _landed(upload.create(body, split=split is not None), split=split is not None)
    finally:
        if session is None:
            upload.client.close()


def _export_filename(response: requests.Response, dataset_id: str, file_format: str) -> str:
    header = (getattr(response, "headers", {}) or {}).get("Content-Disposition", "")
    suggested = Parser().parsestr(f"Content-Disposition: {header}\n").get_filename() if header else None
    return safe_filename(suggested or f"{dataset_id}.{file_format}", fallback=f"dataset.{file_format}")


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
    if output is not None and output.exists():
        raise DatasetExportError(f"Output path already exists: {output}")

    def redact(message: str) -> str:
        return message.replace(api_key, "[redacted]") if api_key else message

    client = open_session(api_key, json_body=False, session=session)
    response = None
    try:
        params = {"fmt": file_format, **({"cell": cell} if cell else {})}
        try:
            response = client.get(
                f"{api_url.rstrip('/')}{EXPORT_PATH.format(dataset_id=quote(dataset_id, safe=''))}",
                params=params,
                timeout=CHUNK_TIMEOUT,
                stream=True,
            )
        except requests.RequestException as exc:
            raise DatasetExportError(f"dataset export failed: {redact(str(exc))}") from exc
        if not response.ok:
            raise DatasetExportError(f"HTTP {response.status_code}: {redact(response_detail(response))}")
        headers = dict(getattr(response, "headers", {}) or {})
        destination = output if output is not None else Path(_export_filename(response, dataset_id, file_format))
        bytes_written = stream_to_new_file(
            response,
            destination,
            chunk_size=EXPORT_CHUNK_SIZE,
            error=DatasetExportError,
            describe_stream_error=lambda exc: f"dataset export stream failed: {redact(str(exc))}",
            label="export",
        )
    finally:
        close_quietly(response)
        if session is None:
            close_quietly(client)

    result: dict[str, Any] = {
        "path": str(destination),
        "dataset_id": dataset_id,
        "format": file_format,
        "bytes_written": bytes_written,
    }
    for key, header in EXPORT_HEADERS:
        if headers.get(header):
            result[key] = str(headers[header])
    return result


@dataset_app.command("upload")
def upload(
    file: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    project_id: ProjectIdOption = "",
    api_key: ApiKeyOption = "",
    api_url: ApiUrlOption = "",
    path: ConfigPathOption = DEFAULT_PATH,
    intent: Annotated[str | None, typer.Option("--intent", help="Dataset intent: train or eval")] = None,
    capability: Annotated[str | None, typer.Option("--capability", help="Optional capability UUID")] = None,
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
    as_json: JsonOption = False,
) -> None:
    """Upload FILE and land it as a dataset, or with --split as a train and an eval dataset."""
    with guard(DatasetUploadError, OSError, ValueError, as_json=as_json):
        connection = Connection.resolve(
            path,
            api_key=api_key,
            api_url=api_url,
            project_id=project_id,
            require_project=True,
            error=DatasetUploadError,
        )
        result = upload_file(
            file,
            project_id=connection.project_id,
            api_key=connection.api_key,
            api_url=connection.base_url,
            intent=intent,
            capability=capability,
            split=split,
            split_position=split_position,
        )
        if wait:
            for id_key, state_key in (("id", "state"), ("eval_id", "eval_state")):
                if id_key in result:
                    ready = wait_until_ready(result[id_key], api_key=connection.api_key, api_url=connection.base_url)
                    result[state_key] = str(ready.get("state") or "")

    if as_json:
        emit_json(result)
        return
    console.print(f"Uploaded {file.name}; dataset {result['id']} is {result['state']}.")
    if "eval_id" in result:
        console.print(f"Eval dataset {result['eval_id']} is {result['eval_state']}.")
    if not wait:
        console.print("Next: get_job(kind=dataset_run, id=<dataset id>), then inspect_dataset.")


@dataset_app.command("export")
def export(
    dataset: Annotated[str, typer.Argument(help="Dataset id")],
    file_format: Annotated[str, typer.Option("--format", help="Export format: jsonl or csv")] = "jsonl",
    cell: Annotated[
        str | None, typer.Option("--cell", help="A cell id or a version such as 1.2; default is the active version")
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Local output path")] = None,
    api_key: ApiKeyOption = "",
    api_url: ApiUrlOption = "",
    path: ConfigPathOption = DEFAULT_PATH,
    as_json: JsonOption = False,
) -> None:
    """Download a dataset version to a new local file."""
    with guard(DatasetExportError, OSError, ValueError, as_json=as_json):
        connection = Connection.resolve(path, api_key=api_key, api_url=api_url, error=DatasetExportError)
        result = export_dataset(
            dataset,
            file_format=file_format,
            cell=cell,
            output=output,
            api_key=connection.api_key,
            api_url=connection.base_url,
        )

    if as_json:
        emit_json(result)
        return
    console.print(f"Exported dataset {result['dataset_id']} to {result['path']} ({result['bytes_written']} bytes).")
    meta = " ".join(f"{key}={result[key]}" for key, _header in EXPORT_HEADERS if result.get(key))
    if meta:
        console.print(meta)
